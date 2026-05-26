"""Shape grouping and keyword extraction for the predicate-paradigm pipeline.

This module runs BEFORE any LLM call. It groups StatVar candidates by shape
fingerprint, derives per-shape slot taxonomies, and extracts shape-discriminating
keyword cues from the query.

Pipeline position:
    retrieve top-K candidates
    → fetch features
    → build_shape_context(query, candidates)   ← this module
    → LLM slot-binding call (slot_binding.py)
    → materialize (predicate.py)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from dataclasses import field as dataclass_field

import cachetools

from dc_search import retrieval as graph
from dc_search.retrieval.indicator import StatVarFeatures
from dc_search.retrieval.topics import TopicMetadata

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Shape:
    """One group of candidates that share the same structural fingerprint.

    Fingerprint = (population_type, measured_property, sorted constraint-key
    tuple).  Members differ only in constraint *values*, so they constitute one
    addressable slot-binding target.

    Namespace-specific behaviour lives in the hook pipeline (hooks.py).
    Topic shapes are identified by the ``is_topic`` flag (derived from the
    member_dcids prefix) rather than a namespace label.
    """

    population_type: str | None
    measured_property: str | None
    # Sorted constraint-property names (keys only, not values).
    constraint_keys: tuple[str, ...]
    member_dcids: tuple[str, ...]  # DCIDs of candidates in this shape
    slot_taxonomy: dict[str, tuple[str, ...]]  # slot → sorted observed values
    is_topic: bool = False
    """True when this shape represents a Topic node (dc/topic/* or ONE/topic/*)."""

    # Structural features from DC's property graph, unioned across members.
    # Empty tuple when the underlying SVs don't expose the property (common for
    # SDG / worldBank / CRS_DAC namespaces; populated for DC-native and WHO).
    # Surfacing these in the slot-binder prompt lets the LLM see modifier signals
    # (per-capita, share-of-GDP, annual, etc.) as structured facts instead of
    # having to infer them from DCID substrings.
    stat_types: tuple[str, ...] = ()
    measurement_qualifiers: tuple[str, ...] = ()
    measurement_denominators: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ShapeContext:
    """Everything the LLM needs to elect a shape and bind slots."""

    query: str
    shapes: tuple[Shape, ...]  # largest shape first
    keyword_cues: dict[str, list[str]]  # cue_type → matched words from query
    topic_metadata: dict[str, TopicMetadata] = dataclass_field(default_factory=dict)
    """Fetched name+description for Topic shapes. Empty when no Topic shapes exist."""
    resolved_places: tuple[tuple[str, str | None, str | None, str], ...] = ()
    """(dcid, canonical_name, input_surface, role) 4-tuples for places resolved from the query.

    ``role`` is one of ``"donor"``, ``"recipient"``, or ``"ambiguous"`` — computed
    once per query from the ORIGINAL full query string by
    ``place_role.place_directional_role``.  The scoped per-variable ``shape_query``
    is used only for LLM shape election; directional role assignment always reads
    the original grammar (Amendment 2).

    Empty on the simple endpoint and when no place resolved.  When populated,
    the slot-binder uses these to offer known places to place-typed constraint
    slots (e.g. DevelopmentFinanceRecipient) and reads the pre-computed role
    instead of re-scanning the (possibly scoped) query.
    """


# ---------------------------------------------------------------------------
# Namespace classification
# ---------------------------------------------------------------------------

# DCID prefixes that unambiguously identify Census-family SVs.
# Order matters: longer / more specific prefixes should be checked before
# the single-word ones. All are case-sensitive (DCIDs are case-sensitive).
_CENSUS_PREFIXES = (
    "LifeExpectancy_",
    "Mortality_",
    "Median_",
    "Count_",
    "Amount_",
    "Mean_",
    "Percent_",
)


def classify_namespace(dcid: str) -> str:
    """Map a StatVar DCID to its namespace class.

    Primary signal is DCID prefix; no network calls, no side effects.

    Mapping:
        ONE/CRS_DAC/          → "CRS_DAC"
        Count_*, Median_*,    → "Census"
          LifeExpectancy_*,
          Amount_*, Mortality_*,
          Mean_*, Percent_*
        sdg/                  → "SDG"
        ONE/who_*, WHO/       → "WHO"
        <anything else>       → "Other"

    Edge cases:
        - CRS_DAC check precedes Census because ONE/CRS_DAC/ starts with
          neither a Census prefix nor "sdg/" nor "ONE/who_".
        - WHO check: ONE/who_ is a specific sub-prefix of ONE/ (our custom
          instance WHO codes), while WHO/ is the base-DC WHO namespace.
        - SDG check: sdg/ is lower-case, case-sensitive.
        - Candidates whose populationType is MortalityEvent but whose DCID
          starts with Count_ still classify as Census — the DCID prefix is
          the authoritative signal (e.g. Count_MortalityEvent_* → Census).
    """
    if dcid.startswith("dc/topic/") or dcid.startswith("ONE/topic/"):
        return "Topic"
    if dcid.startswith("ONE/CRS_DAC/"):
        return "CRS_DAC"
    if any(dcid.startswith(p) for p in _CENSUS_PREFIXES):
        return "Census"
    if dcid.startswith("sdg/"):
        return "SDG"
    if dcid.startswith("ONE/who_") or dcid.startswith("WHO/"):
        return "WHO"
    return "Other"


# ---------------------------------------------------------------------------
# Shape grouping
# ---------------------------------------------------------------------------


def _is_topic_dcid(dcid: str) -> bool:
    """Return True when ``dcid`` is a Topic node (structural prefix check).

    Topic DCIDs use ``dc/topic/`` (Data Commons curated) or ``ONE/topic/``
    (ONE-org custom) prefixes.  This is a structural detection of one node
    type, not namespace classification.
    """
    return dcid.startswith("dc/topic/") or dcid.startswith("ONE/topic/")


# Fingerprint type alias: (pop_type_or_topic_dcid, meas_prop, constraint_keys)
# For Topic nodes the first element is the DCID (ensures uniqueness).
# For all other nodes it is the populationType (may be None).
_Fingerprint = tuple[str | None, str | None, tuple[str, ...]]


def _fingerprint(svf: StatVarFeatures) -> _Fingerprint:
    """Derive the shape fingerprint for one candidate.

    Fingerprint is ``(populationType, measuredProperty, sorted_constraint_keys)``
    for most SVs.  For Topic nodes, the DCID is used as the first element to
    prevent collapse (each Topic is its own atom, not structurally
    distinguishable by populationType).
    """
    if _is_topic_dcid(svf.dcid):
        # Each topic is its own shape — use DCID to prevent collapse.
        return (svf.dcid, None, ())
    pop_type = svf.population_type[0] if svf.population_type else None
    meas_prop = svf.measured_property[0] if svf.measured_property else None
    constraint_keys = tuple(sorted(svf.constraints.keys()))
    return (pop_type, meas_prop, constraint_keys)


def _build_slot_taxonomy(members: list[StatVarFeatures]) -> dict[str, tuple[str, ...]]:
    """Build the slot taxonomy for a shape group.

    Returns {constraint_key: sorted tuple of all observed values across members}.

    Only constraint slots are included, not the lifted axes
    (populationType, measuredProperty, measurementDenominator). Within a single
    shape those axes don't vary.
    """
    accumulated: dict[str, set[str]] = {}
    for svf in members:
        for key, vals in svf.constraints.items():
            accumulated.setdefault(key, set()).update(vals)
    return {k: tuple(sorted(v)) for k, v in accumulated.items()}


def _union_values(members: list[StatVarFeatures], attr: str) -> tuple[str, ...]:
    """Union a list-valued StatVarFeatures attribute across shape members.

    Members in a shape share the structural fingerprint but may carry slightly
    different annotation values (e.g. one member declares a denominator and
    another doesn't). Returning the sorted union surfaces every distinct value
    while keeping the output deterministic.
    """
    accumulated: set[str] = set()
    for svf in members:
        for v in getattr(svf, attr) or ():
            if v:
                accumulated.add(v)
    return tuple(sorted(accumulated))


# ---------------------------------------------------------------------------
# Place-token extraction — n-gram + stop-list + resolve_place
# ---------------------------------------------------------------------------

# Function words: never constitute a place name on their own (or as the
# sole content of an n-gram).
_PLACE_STOPLIST_FUNCTION: frozenset[str] = frozenset(
    {
        "the",
        "of",
        "in",
        "to",
        "and",
        "or",
        "for",
        "by",
        "on",
        "a",
        "an",
        "at",
        "from",
        "into",
        "with",
        "without",
        "as",
        # Temporal / relational connectives in date phrasings ("X between 2005
        # and 2015"). Without these the token fallback resolves the connective to
        # a homograph place — e.g. "between" -> Between, GA (geoId/1307640) —
        # which silently breaks downstream date filtering.
        "between",
        "since",
        "before",
        "after",
        "during",
        "over",
        "until",
        "through",
    }
)

# Domain-specific lemmas: common metric, policy, and topic words that happen
# to share a surface form with real place names (e.g. "grants" → Grants NM).
_PLACE_STOPLIST_DOMAIN: frozenset[str] = frozenset(
    {
        # generic metric lemmas
        "population",
        "mortality",
        "rate",
        "share",
        "count",
        "deaths",
        "births",
        "incidence",
        "prevalence",
        "cases",
        "indicator",
        "indicators",
        "level",
        "amount",
        "median",
        "mean",
        "percentage",
        "percent",
        "fraction",
        "ratio",
        # generic policy/finance lemmas (CRS_DAC frequent words)
        "grants",
        "grant",
        "aid",
        "oda",
        "funding",
        "spend",
        "spending",
        "donor",
        "recipient",
        # query-prefix words
        "how",
        "what",
        "which",
        "total",
        "global",
        "world",
        "all",
        "did",
        "much",
        "many",
        "most",
        "least",
        # disease and topic lemmas observed in eval
        "malaria",
        "hiv",
        "aids",
        "tuberculosis",
        "tb",
        "covid",
        "polio",
        "neonatal",
        "infant",
        "maternal",
        "child",
        "youth",
        "adolescent",
        # general nouns
        "people",
        "women",
        "men",
        "children",
        "adults",
        "person",
        # misc query words
        "projected",
        "max",
        "temperature",
        "decade",
        "year",
        "annual",
        "growth",
        "gdp",
        "per",
        "capita",
        # ambiguous policy/programme lemmas (geoId homographs)
        "control",
        # continent / UN-region names that resolve to non-country DCIDs and
        # would otherwise pollute availability sets
        "africa",
        "europe",
        "asia",
        "americas",
        "america",
        "latin",
        "oceania",
    }
)

# Combined stop-word set; membership check is case-insensitive (lowercase before
# looking up). An n-gram is dropped iff EVERY token is in this set.
_PLACE_STOPWORDS: frozenset[str] = _PLACE_STOPLIST_FUNCTION | _PLACE_STOPLIST_DOMAIN

# Punctuation that splits tokens (but NOT hyphen or apostrophe).
_TOKEN_SPLIT_RE = re.compile(r"[\s,;/]+")

# LRU cache replacing the @functools.cache decorator on extract_place_tokens.
# Bounded to prevent unbounded memory growth in a long-running gunicorn worker.
_extract_place_tokens_cache: cachetools.LRUCache = cachetools.LRUCache(maxsize=512)


def _tokenize(text: str) -> list[str]:
    """Split query into tokens on whitespace and selected punctuation.

    Splits on: whitespace, comma, semicolon, forward-slash.
    Does NOT split on: hyphen (under-5), apostrophe (People's).
    Empty strings produced by leading/trailing delimiters are dropped.
    """
    return [t for t in _TOKEN_SPLIT_RE.split(text) if t]


def _is_all_stopwords(tokens: list[str]) -> bool:
    """Return True if every token (case-insensitive) is in _PLACE_STOPWORDS."""
    return all(t.lower() in _PLACE_STOPWORDS for t in tokens)


def extract_place_tokens(query: str) -> list[str]:
    """Return resolved DCIDs of place mentions in *query*, in reading order.

    Pipeline:
      1. Tokenize on whitespace + selected punctuation, preserving case.
      2. Generate contiguous n-grams of length 1..max_ngram (= 6), dropping
         any n-gram whose tokens are ALL in ``_PLACE_STOPWORDS``.
      3. For each surviving n-gram, call ``graph.resolve_place(name=ngram)``.
         Keep iff resolution returns a non-empty candidate tuple; accept the
         top candidate's DCID unconditionally (no prefix gate — the stop-list
         is the primary guard against false positives such as Grants NM).
      4. Span maximality — prefer the LONGEST resolved span; once a span's
         token positions are claimed, drop shorter overlapping resolved spans
         whose tokens are entirely claimed.
      5. De-duplicate by resolved DCID, keeping the first DCID encountered in
         reading order.

    Returns:
        Deduplicated list of resolved DCIDs (e.g. ``["country/GTM"]``).
        Downstream consumers (agent._resolve_union_availability) feed these
        directly to ``graph.variables_for_entity`` without a second resolve
        call.

    Notes:
        ``graph.resolve_place`` is ``@cache``'d, so repeat n-grams across
        queries cost zero network round-trips. This function caches results
        per query string via a module-level LRUCache.

        Fails open per candidate: if ``resolve_place`` raises for one n-gram
        the exception is swallowed (logged at DEBUG) and that candidate is
        skipped.

        Non-English queries and DCID-in-query are out of scope for V1.
    """
    cached = _extract_place_tokens_cache.get(query)
    if cached is not None:
        return cached

    result = _extract_place_tokens_impl(query)
    _extract_place_tokens_cache[query] = result
    return result


def _extract_place_tokens_impl(query: str) -> list[str]:
    tokens = _tokenize(query)
    if not tokens:
        return []

    max_ngram = 6
    n = len(tokens)

    # --- Step 2: generate n-grams, drop all-stopword spans ---
    # Collect candidate spans first (longest-first ordering matters for
    # maximality below) so we can resolve them all in ONE batched HTTP call.
    candidate_spans: list[tuple[int, int, str]] = []  # (start, end, surface)
    for length in range(max_ngram, 0, -1):
        for start in range(n - length + 1):
            span_tokens = tokens[start : start + length]
            if _is_all_stopwords(span_tokens):
                continue
            candidate_spans.append((start, start + length, " ".join(span_tokens)))

    # --- Step 3: batch-resolve every surface in one /v2/resolve call ---
    # graph.resolve_places_batch caches per-surface (including negatives), so
    # repeat surfaces across queries cost zero network round-trips. A single
    # call replaces N (~13 in typical 5-token queries) sequential round-trips
    # — ~20x speedup measured against a custom DC instance.
    surfaces = tuple({surface for _, _, surface in candidate_spans})
    try:
        resolved_map = graph.resolve_places_batch(names=surfaces)
    except Exception:
        _log.warning("resolve_places_batch raised on %d surfaces; skipping", len(surfaces))
        resolved_map = {}

    resolved_spans: list[tuple[int, int, str, str]] = []
    for start, end, surface in candidate_spans:
        candidates = resolved_map.get(surface, ())
        if candidates:
            resolved_spans.append((start, end, surface, candidates[0].dcid))

    # --- Step 4: span maximality ---
    # Sort by span length descending (already in that order from the loop),
    # then claim token positions.
    claimed: set[int] = set()
    kept_spans: list[tuple[int, int, str, str]] = []
    for start, end, surface, dcid in resolved_spans:
        positions = set(range(start, end))
        if positions <= claimed:
            # All positions already claimed by a longer span — drop.
            continue
        claimed.update(positions)
        kept_spans.append((start, end, surface, dcid))

    # --- Step 5: de-duplicate by DCID, preserving reading order ---
    # Sort kept_spans by start index so we iterate in reading order.
    kept_spans.sort(key=lambda t: t[0])
    seen_dcids: set[str] = set()
    result: list[str] = []
    for _start, _end, _surface, dcid in kept_spans:
        if dcid not in seen_dcids:
            seen_dcids.add(dcid)
            result.append(dcid)

    return result


def extract_keyword_cues(query: str) -> dict[str, list[str]]:
    """Extract structural cues from the query string.

    Returns ``{"place_dcids": [resolved place DCIDs, e.g. "country/GTM"]}``.

    The ``place_dcids`` key contains resolved DCIDs (not surface strings).
    Downstream consumers iterate ``place_dcids`` and call
    ``variables_for_entity`` directly.
    """
    return {"place_dcids": extract_place_tokens(query)}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def build_shape_context(
    query: str,
    candidates: list[StatVarFeatures],
    retrieval_scores: dict[str, float] | None = None,
    *,
    resolved_places: tuple[tuple[str, str | None, str | None, str], ...] = (),
    max_shapes: int | None = None,
) -> ShapeContext:
    """Group candidates by shape, derive slot taxonomies, extract keyword cues.

    Steps:
        1. Filter: drop candidates that have BOTH populationType AND
           measuredProperty empty (true topic-style nodes with no usable
           grouping signal). Candidates with only one of the two populated
           are kept — many real SVs (notably WHO indicator codes like
           ONE/who_hf3) declare populationType but not measuredProperty.
        2. Group by fingerprint: (populationType[0], measuredProperty[0],
           sorted constraint-key tuple). Either of the two first fields may
           be None.
        3. Build a Shape for each group: slot taxonomy = union of observed
           constraint values across members.
        4. Sort shapes: when ``retrieval_scores`` is provided, sort by the
           maximum retrieval score among members (highest first); otherwise
           fall back to member count (largest first). Retrieval-score sort
           ensures a high-scoring exact-match SV surfaces as Shape 0 ahead
           of a lower-scoring Topic shape.
        5. Extract keyword cues from the query.

    Args:
        query: The user's natural-language query.
        candidates: Batch-fetched StatVarFeatures from graph.stat_var_features_batch.
        retrieval_scores: Optional mapping from SV DCID to retrieval score
            (0.0-1.0). When provided, shapes are sorted by
            ``max(score for member in shape)`` descending; ties broken by
            member count. Defaults to ``None`` (member-count sort) so
            existing callers and tests remain unaffected.
        max_shapes: Optional cap on the number of shapes returned, applied
            after the sort. ``None`` (default) returns all shapes. The
            candidate/feature pool is unaffected, so materialization still
            binds against every member of whichever shape the slot-binder
            elects.

    Returns:
        ShapeContext with shapes (ordered by retrieval score or member count,
        truncated to ``max_shapes`` when set) and keyword cues.
    """
    # Step 1: drop candidates with NO grouping signal (both fields empty),
    # UNLESS the candidate is a Topic node — Topics have neither populationType
    # nor measuredProperty and are kept via the DCID prefix.
    filtered = [
        svf
        for svf in candidates
        if (svf.population_type and svf.population_type[0])
        or (svf.measured_property and svf.measured_property[0])
        or _is_topic_dcid(svf.dcid)
    ]

    # Step 2: group by structural fingerprint.
    groups: dict[_Fingerprint, list[StatVarFeatures]] = {}
    for svf in filtered:
        fp = _fingerprint(svf)
        groups.setdefault(fp, []).append(svf)

    # Step 3: build Shape objects.
    shapes: list[Shape] = []
    for (pop_type_or_dcid, meas_prop, c_keys), members in groups.items():
        is_topic = any(_is_topic_dcid(svf.dcid) for svf in members)
        if is_topic:
            # Each Topic gets its own Shape with population_type=None.
            # The fingerprint's first element is the DCID (uniqueness key).
            shapes.append(
                Shape(
                    population_type=None,
                    measured_property=None,
                    constraint_keys=(),
                    member_dcids=tuple(svf.dcid for svf in members),
                    slot_taxonomy={},
                    is_topic=True,
                )
            )
        else:
            shapes.append(
                Shape(
                    population_type=pop_type_or_dcid,
                    measured_property=meas_prop,
                    constraint_keys=c_keys,
                    member_dcids=tuple(svf.dcid for svf in members),
                    slot_taxonomy=_build_slot_taxonomy(members),
                    is_topic=False,
                    stat_types=_union_values(members, "stat_type"),
                    measurement_qualifiers=_union_values(members, "measurement_qualifier"),
                    measurement_denominators=_union_values(members, "measurement_denominator"),
                )
            )

    # Step 4: sort shapes — by max retrieval score when scores are available,
    # falling back to member count. This ensures a high-scoring structured SV
    # ranks above a lower-scoring Topic shape, so the LLM sees the best
    # structural candidate as Shape 0.
    if retrieval_scores:
        scores = retrieval_scores  # narrow for the closure below

        def _sort_key(s: Shape) -> tuple[float, int]:
            max_score = max(
                (scores.get(d, 0.0) for d in s.member_dcids),
                default=0.0,
            )
            return (max_score, len(s.member_dcids))

        shapes.sort(key=_sort_key, reverse=True)
    else:
        shapes.sort(key=lambda s: len(s.member_dcids), reverse=True)

    # Cap the shapes shown to the slot-binding LLM, applied AFTER the sort so the
    # highest-scoring shapes survive. Bounds LLM prompt noise; the candidate /
    # feature pool is untouched, so materialization still binds against the full
    # membership of whichever shape the LLM elects.
    if max_shapes is not None:
        shapes = shapes[:max_shapes]

    # Step 5: extract keyword cues.
    keyword_cues = extract_keyword_cues(query)

    return ShapeContext(
        query=query,
        shapes=tuple(shapes),
        keyword_cues=keyword_cues,
        resolved_places=resolved_places,
    )
