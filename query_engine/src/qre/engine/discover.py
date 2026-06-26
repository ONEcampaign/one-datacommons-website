"""Graph-touching shape discovery and general resolution helpers.

This is the ONLY module (besides retrieve.py and resolver resolve() methods)
that may issue graph reads inside the engine pipeline.  shape.py, axis.py,
families/protocol.py, families/registry.py, and families/dev_finance.py are
all pure (no graph I/O).

Public surface:
  read_five_tuple(sv_arcs)            -> FiveTuple (namedtuple)
  read_constraints(sv_arcs)           -> dict[prop_dcid, value_dcid]
  read_slot_taxonomy(*, shape_draft, confirmed_svs, graph) -> dict[str, list[str]]
  derive_shapes(*, confirmed_svs, graph) -> list[ShapeDraft]
  filter_offtopic_shapes(shapes, *, variable) -> list[ShapeDraft]
  graph_confirm_resolve(*, shape, bindings, recipient_dcid, donor_dcid, graph)
                                      -> Materialised | NoDataDraft

derive_shapes is called by core.py and NEVER by a resolver.  Each returned
ShapeDraft is stamped with its matched FamilyRule and carries the per-SV arc
facts read here once, so resolvers never re-read node_arcs.
Each ShapeDraft also carries slot_taxonomy, populated by read_slot_taxonomy so
core.py can build the bind prompt without any further graph reads.

graph_confirm_resolve is the general resolution helper used by resolvers that
do not have a bespoke constructor (e.g. StandardResolver).  It
consumes the per-SV arc facts already on the ShapeDraft to filter SVs
whose constraint values match the bound slot values, then probes observations.
"""
from __future__ import annotations

import dataclasses
import logging
import re
from collections import defaultdict
from typing import TYPE_CHECKING, NamedTuple

from qre.engine.config import QRE_MAX_CONFIRM_CANDIDATES
from qre.engine.coverage import coverage_from_facets
from qre.engine.families.registry import rule_for
from qre.engine.graph import EngineGraphClient, Facet
from qre.engine.shape import ShapeDraft, shape_draft_from

if TYPE_CHECKING:
    from qre.engine.bind import SlotBindingDraft
    from qre.engine.extract import DateRequest
    from qre.engine.retrieve import Materialised, NoDataDraft

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Five-tuple
# ---------------------------------------------------------------------------

class FiveTuple(NamedTuple):
    """The five-tuple that identifies a shape group."""

    pop_type_dcid: str
    meas_prop_dcid: str
    stat_type_dcid: str
    meas_qual_dcid: str | None
    meas_denom_dcid: str | None


# ---------------------------------------------------------------------------
# Arc readers (pure — operate on an already-fetched arcs dict)
# ---------------------------------------------------------------------------

def _dcids_for(arcs: dict, prop: str) -> list[str]:
    """Extract dcid values from an arcs entry."""
    nodes = arcs.get(prop, {}).get("nodes", [])
    return [n["dcid"] for n in nodes if "dcid" in n]


def _value_for(arcs: dict, prop: str) -> str | None:
    """Extract a single string value from an arcs entry (for literal props)."""
    nodes = arcs.get(prop, {}).get("nodes", [])
    for n in nodes:
        if "value" in n:
            return n["value"]
    dcids = _dcids_for(arcs, prop)
    return dcids[0] if dcids else None


def read_five_tuple(sv_arcs: dict) -> FiveTuple:
    """Extract the five-tuple from a node_arcs result dict.

    Args:
        sv_arcs: The arcs dict returned by graph.node_arcs(sv_dcid).

    Returns:
        FiveTuple with the five measurement dimensions.  Missing optional
        dimensions (measurementQualifier, measurementDenominator) are None.
    """
    pop_type = _value_for(sv_arcs, "populationType") or ""
    meas_prop = _value_for(sv_arcs, "measuredProperty") or ""
    stat_type = _value_for(sv_arcs, "statType") or ""
    meas_qual = _value_for(sv_arcs, "measurementQualifier")
    meas_denom = _value_for(sv_arcs, "measurementDenominator")
    return FiveTuple(
        pop_type_dcid=pop_type,
        meas_prop_dcid=meas_prop,
        stat_type_dcid=stat_type,
        meas_qual_dcid=meas_qual,
        meas_denom_dcid=meas_denom,
    )


def read_constraints(sv_arcs: dict) -> dict[str, str]:
    """Extract constraint property → value dcid from a node_arcs result dict.

    Uses the constraintProperties list to determine which arcs are constraints,
    then reads each constraint property's value.

    Args:
        sv_arcs: The arcs dict returned by graph.node_arcs(sv_dcid).

    Returns:
        A dict mapping constraint property dcid to its value dcid for this SV.
        Properties with no value in the arcs are omitted.
    """
    constraint_prop_dcids = _dcids_for(sv_arcs, "constraintProperties")
    result: dict[str, str] = {}
    for prop_dcid in constraint_prop_dcids:
        value = _value_for(sv_arcs, prop_dcid)
        if value is not None:
            result[prop_dcid] = value
    return result


# ---------------------------------------------------------------------------
# read_slot_taxonomy
# ---------------------------------------------------------------------------

def read_slot_taxonomy(
    *,
    shape_draft: "ShapeDraft",
    graph: EngineGraphClient,
) -> dict[str, list[str]]:
    """Build the bind slot taxonomy for a shape.

    For dev-finance (family_rule carries a resolver with slot_taxonomy_seed),
    uses the hand-verified seed so the full scheme/purpose lists reach the binder.

    For standard shapes, builds the observed-union taxonomy from the per-SV arc
    facts already carried on the ShapeDraft (no fresh graph reads).

    The where slot for the recipient entity is NOT included here; core.py injects
    it after deterministic entity resolution.

    Args:
        shape_draft: The ShapeDraft from derive_shapes (carries family_rule and
                     sv_arc_facts).
        graph:       Graph client (unused currently; reserved for future use).

    Returns:
        A dict mapping "axis:property_dcid" to a list of realizable dcids.
        The where slot (recipient) is excluded — core.py adds it separately.
    """
    rule = shape_draft.family_rule

    # Dev-finance path: use the seed from the resolver
    if rule is not None and hasattr(rule.resolver, "slot_taxonomy_seed"):
        seed: dict[str, list[str]] = rule.resolver.slot_taxonomy_seed
        taxonomy: dict[str, list[str]] = {}
        for slot in shape_draft.slot_keys:
            prop = slot.property_dcid
            if prop is None:
                continue  # skip when/source slots (no property)
            if prop in seed:
                taxonomy[f"{slot.axis}:{prop}"] = list(seed[prop])
        return taxonomy

    # Standard path: build from already-read arc facts
    sv_arc_facts = shape_draft.sv_arc_facts or {}

    # Collect observed values per constraint prop across all member SVs
    observed: dict[str, list[str]] = {}
    for _sv_dcid, arcs in sv_arc_facts.items():
        constraints = read_constraints(arcs)
        for prop_dcid, value_dcid in constraints.items():
            observed.setdefault(prop_dcid, [])
            if value_dcid not in observed[prop_dcid]:
                observed[prop_dcid].append(value_dcid)

    taxonomy = {}
    for slot in shape_draft.slot_keys:
        prop = slot.property_dcid
        if prop is None:
            continue  # skip when/source slots
        values = observed.get(prop, [])
        if values:
            taxonomy[f"{slot.axis}:{prop}"] = values

    return taxonomy


# ---------------------------------------------------------------------------
# filter_offtopic_shapes
# ---------------------------------------------------------------------------

# Stopwords removed from the variable phrase and the SV label before matching.
# Kept minimal so semantically meaningful words (rate, total, number) contribute.
_FILTER_STOPWORDS = frozenset({"of", "in", "the", "a", "per", "to", "from", "for"})

# Prefix length for stem comparison: "population" and "populations" both stem to "popu".
_STEM_PREFIX = 4

# Normalise punctuation (commas, parens, slashes, periods, underscores) to spaces.
_PUNCT_RE = re.compile(r"[^\w]|_", re.ASCII)

# Split on CamelCase boundaries: "BirthEvent" → "Birth Event".
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _content_stems(text: str) -> frozenset[str]:
    """Extract 4-char prefix stems: lowercase, normalize punctuation, drop stopwords.

    Also handles CamelCase splitting so "BirthEvent" produces "birth" and "event".
    """
    # Normalise punctuation and camel case, then lowercase
    normalised = _PUNCT_RE.sub(" ", _CAMEL_RE.sub(" ", text)).lower()
    return frozenset(
        w[:_STEM_PREFIX]
        for w in normalised.split()
        if w not in _FILTER_STOPWORDS and len(w) > 1
    )


def filter_offtopic_shapes(
    shapes: list[ShapeDraft],
    *,
    variable: str,
) -> list[ShapeDraft]:
    """Drop STANDARD shapes whose representative SV label shares no content-word token
    with the variable phrase.

    Removes noise candidates (e.g. "Percent of Internet Users" from a "total population"
    query) while keeping shapes whose label overlaps the variable on at least one
    stemmed content word.

    Only STANDARD_RULE shapes are filtered; dev-finance shapes pass through untouched.

    Matching strategy:
      1. Primary: 4-char prefix stem overlap between variable and name arc value.
      2. Fallback: 4-char prefix stem overlap between variable and representative
         SV dcid (CamelCase-split).  This catches cases where a label is abstract.

    The representative SV label is read from sv_arc_facts[rep_sv] via the existing
    _value_for(arcs, "name") call.  When the name arc is absent the shape is kept.

    Defensive: never filter when the variable has fewer than two content-word stems
    (acronyms and short terms are too unreliable to filter).

    Args:
        shapes:   ShapeDraft list, may contain any mix of families.
        variable: The extracted variable phrase (first variable from Extraction).

    Returns:
        Filtered list with off-topic standard shapes removed.
    """
    # Lazy import to avoid a circular dependency (registry imports shape, not discover)
    from qre.engine.families.registry import STANDARD_RULE  # noqa: PLC0415

    var_stems = _content_stems(variable)
    # Defensive: never filter when the variable has fewer than two content-word stems.
    # Single-stem variables (e.g. "GDP") are too short for reliable matching.
    if len(var_stems) < 2:
        return list(shapes)

    kept: list[ShapeDraft] = []
    for shape in shapes:
        if shape.family_rule is not STANDARD_RULE:
            # Non-standard (dev-finance, etc.) always pass through.
            kept.append(shape)
            continue

        # Read the representative SV's display name from the already-fetched arc facts.
        arc_facts = shape.sv_arc_facts or {}
        rep_sv = next(iter(arc_facts), None)
        if rep_sv is None:
            # No arc facts — keep (cannot evaluate).
            kept.append(shape)
            continue

        rep_arcs = arc_facts[rep_sv]
        name = _value_for(rep_arcs, "name")
        if name is None:
            # Missing name arc — keep (never drop a shape on a missing label).
            kept.append(shape)
            continue

        # Primary: label → variable stem overlap.
        label_stems = _content_stems(name)
        if label_stems & var_stems:
            kept.append(shape)
            continue

        # Fallback: dcid → variable stem overlap.
        dcid_stems = _content_stems(rep_sv)
        if dcid_stems & var_stems:
            kept.append(shape)

    return kept


# ---------------------------------------------------------------------------
# derive_shapes
# ---------------------------------------------------------------------------

def derive_shapes(
    *,
    confirmed_svs: list[str],
    graph: EngineGraphClient,
    sv_scores: dict[str, float] | None = None,
) -> list[ShapeDraft]:
    """Confirm each candidate SV and build a ShapeDraft per distinct five-tuple.

    Steps:
    1. Cap candidates at QRE_MAX_CONFIRM_CANDIDATES.
    2. For each candidate, call node_arcs.  Drop any SV that returns None.
    3. Read the five-tuple and constraint properties from the arcs.
    4. Group confirmed SVs by five-tuple.
    5. Build one ShapeDraft per group via shape_draft_from (pure; no I/O).
       - Each ShapeDraft is stamped with its matched FamilyRule and carries the
         per-SV arc facts so resolvers never re-read node_arcs.

    Args:
        confirmed_svs: Candidate SV dcids from the recall stage.
        graph:         Graph client for node_arcs calls.
        sv_scores:     Optional dcid→cosine-score map from the recall stage. When
                       provided, each ShapeDraft is stamped with the score of its
                       representative SV (first SV in insertion order). Defaults to 1.0.

    Returns:
        List of ShapeDrafts (one per distinct five-tuple among confirmed SVs).
        Empty when no candidate confirms.
    """
    # Cap confirm loop
    candidates = confirmed_svs[:QRE_MAX_CONFIRM_CANDIDATES]
    if len(confirmed_svs) > QRE_MAX_CONFIRM_CANDIDATES:
        logger.debug(
            "derive_shapes: capped %d candidates to %d (QRE_MAX_CONFIRM_CANDIDATES)",
            len(confirmed_svs),
            QRE_MAX_CONFIRM_CANDIDATES,
        )

    # Shared label cache across groups: property dcids are stable schema nodes
    # (same dcid always has the same label), so we call node_label at most once
    # per distinct property dcid across the entire derive_shapes call.
    _label_cache: dict[str, str] = {}

    def _prop_label(prop_dcid: str) -> str:
        if prop_dcid not in _label_cache:
            lbl = graph.node_label(prop_dcid)
            _label_cache[prop_dcid] = lbl if lbl is not None else prop_dcid
        return _label_cache[prop_dcid]

    # Confirm each SV and read arc facts
    sv_facts: dict[str, dict] = {}
    for sv_dcid in candidates:
        arcs = graph.node_arcs(sv_dcid)
        if arcs is None:
            logger.debug("derive_shapes: dropped unconfirmable sv %r", sv_dcid)
            continue
        five_tuple = read_five_tuple(arcs)
        constraints = read_constraints(arcs)
        if not five_tuple.pop_type_dcid:
            logger.debug("derive_shapes: dropped sv with empty populationType %r", sv_dcid)
            continue
        sv_facts[sv_dcid] = {
            "five_tuple": five_tuple,
            "constraints": constraints,
            "arcs": arcs,
        }

    if not sv_facts:
        return []

    # Group confirmed SVs by five-tuple
    groups: dict[FiveTuple, list[str]] = defaultdict(list)
    for sv_dcid, facts in sv_facts.items():
        groups[facts["five_tuple"]].append(sv_dcid)

    # Build one ShapeDraft per group
    shapes: list[ShapeDraft] = []
    for five_tuple, group_svs in groups.items():
        # Collect constraint properties (union across SVs in this group)
        prop_observed_values: dict[str, list[str]] = defaultdict(list)
        group_arc_facts: dict[str, dict] = {}

        for sv_dcid in group_svs:
            facts = sv_facts[sv_dcid]
            constraints = facts["constraints"]
            group_arc_facts[sv_dcid] = facts["arcs"]
            for prop_dcid, value_dcid in constraints.items():
                prop_observed_values[prop_dcid].append(value_dcid)

        constraint_props = list(prop_observed_values.keys())

        # Build prop_labels using the shared cache
        prop_labels: dict[str, str] = {p: _prop_label(p) for p in constraint_props}

        # Determine shape_id and label from the matched family rule
        matched_rule = rule_for(candidate_svs=group_svs)
        _rule_shape_id = (
            (matched_rule.shape_id or matched_rule.namespace.rstrip("/")) if matched_rule else ""
        )
        if _rule_shape_id:
            shape_id = _rule_shape_id
            shape_label = matched_rule.label
        else:
            # Standard: use five-tuple string as identity for deduplication
            shape_id = (
                f"{five_tuple.pop_type_dcid}_{five_tuple.meas_prop_dcid}"
                f"_{five_tuple.stat_type_dcid}"
                + (f"_{five_tuple.meas_qual_dcid}" if five_tuple.meas_qual_dcid else "")
                + (f"_per_{five_tuple.meas_denom_dcid}" if five_tuple.meas_denom_dcid else "")
            ).lower()
            shape_label = matched_rule.label if matched_rule else five_tuple.pop_type_dcid

        rep_sv_dcid = group_svs[0]
        rep_score = (sv_scores or {}).get(rep_sv_dcid, 1.0)

        shape = shape_draft_from(
            shape_id=shape_id,
            label=shape_label,
            pop_type_dcid=five_tuple.pop_type_dcid,
            meas_prop_dcid=five_tuple.meas_prop_dcid,
            stat_type_dcid=five_tuple.stat_type_dcid,
            meas_qual_dcid=five_tuple.meas_qual_dcid,
            meas_denom_dcid=five_tuple.meas_denom_dcid,
            constraint_props=constraint_props,
            prop_labels=prop_labels,
            prop_observed_values=dict(prop_observed_values),
            family_rule=matched_rule,
            sv_arc_facts=group_arc_facts,
            representative_score=rep_score,
        )
        taxonomy = read_slot_taxonomy(shape_draft=shape, graph=graph)
        shape = dataclasses.replace(shape, slot_taxonomy=taxonomy)
        shapes.append(shape)

    return shapes


# ---------------------------------------------------------------------------
# General resolution helper (used by resolvers that have no bespoke constructor)
# ---------------------------------------------------------------------------

# Default donor for has_data probes when no specific donor is named.
_DEFAULT_PROBE_DONOR = "country/USA"


def graph_confirm_resolve(
    *,
    shape: ShapeDraft,
    bindings: "list[SlotBindingDraft]",
    recipient_dcid: str | None,
    donor_dcid: str | None,
    graph: EngineGraphClient,
    date_request: "DateRequest | None" = None,
    facet_label: str = "sources",
    obs_label: str = "observations",
) -> "Materialised | NoDataDraft":
    """General resolution helper: filter confirmed SVs by bound constraint values.

    Consumes the per-SV arc facts already carried on the ShapeDraft (no re-read).
    For each confirmed member SV, checks that its constraint property values match
    the bound slot values.  Then probes observations on the surviving SVs.

    This is the default path for families without a bespoke constructor
    (e.g. StandardResolver).  DevFinanceResolver calls it as a fallback when
    construct_sv_dcid yields nothing.

    Args:
        shape:          ShapeDraft carrying member SVs and their arc facts.
        bindings:       Slot bindings from the LLM bind stage.
        recipient_dcid: Resolved recipient entity dcid, or None.
        donor_dcid:     Resolved donor entity dcid, or None.
        graph:          Graph client (injected; use FakeGraph in tests).
        facet_label:    Label for the per-facet-count coverage dimension. Defaults to
                        the generic "sources"; DevFinanceResolver passes "donors" so its
                        fallback coverage matches the construct path's dimensions.
        obs_label:      Label for the max-obs-count coverage dimension (generic
                        "observations"; dev-finance passes "years").

    Returns:
        Materialised on success, NoDataDraft on any data-absence outcome.
    """
    # Import here to avoid a circular import at module level (retrieve imports discover).
    from qre.engine.retrieve import Materialised, NoDataDraft  # noqa: PLC0415

    if recipient_dcid is None:
        return NoDataDraft(reason="variable_not_resolved")

    probe_entity = recipient_dcid
    probe_donor = donor_dcid or _DEFAULT_PROBE_DONOR

    # Build a lookup of bound constraint values: prop_dcid -> list[value_dcid]
    bound_values: dict[str, list[str]] = {}
    for b in bindings:
        if b.property_dcid and b.kind in ("value", "set") and b.value_dcids:
            bound_values[b.property_dcid] = list(b.value_dcids)

    # Collect member SVs from the shape's arc facts (no re-read).
    sv_arc_facts = shape.sv_arc_facts or {}
    if not sv_arc_facts:
        return NoDataDraft(reason="variable_not_resolved")

    # Filter: keep SVs whose constraint values match bindings.
    # Unbound or absent slots pass any value.
    surviving_svs: list[str] = []
    for sv_dcid, arcs in sv_arc_facts.items():
        constraints = read_constraints(arcs)
        keep = True
        for prop_dcid, sv_value in constraints.items():
            if prop_dcid in bound_values:
                if sv_value not in bound_values[prop_dcid]:
                    keep = False
                    break
        if keep:
            surviving_svs.append(sv_dcid)

    if not surviving_svs:
        return NoDataDraft(reason="variable_not_resolved")

    # Probe observations for each surviving SV.
    confirmed_svs: list[str] = []
    all_facets: list[Facet] = []
    for sv_dcid in surviving_svs:
        facets = graph.observation_facets(stat_var=sv_dcid, entity=probe_entity)
        if not facets:
            facets = graph.observation_facets(stat_var=sv_dcid, entity=probe_donor)
        if facets and any(f.obs_count > 0 for f in facets):
            confirmed_svs.append(sv_dcid)
            all_facets.extend(facets)

    if not confirmed_svs:
        return NoDataDraft(reason="no_observations")

    coverage = coverage_from_facets(
        all_facets, date_request=date_request, facet_label=facet_label, obs_label=obs_label
    )
    return Materialised(
        sv_dcids=confirmed_svs,
        facets=all_facets,
        has_data=True,
        coverage=coverage,
    )
