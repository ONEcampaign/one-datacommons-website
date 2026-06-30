"""QRE per-variable pipeline: RegionResult + resolve_variable + grounding helpers.

Each variable resolves to one RegionResult with status, specs, warnings, and timing.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from dataclasses import dataclass

from qre.engine.assemble import (
    bind_when_slot,
    build_shape_model,
    build_slot,
    build_spec,
    build_stat_vars,
    make_pipeline_step,
    now_ms,
)
from qre.engine.bind import SlotBindingDraft, _BindOutput, bind
from qre.engine.config import (
    QRE_DOMINANCE_MARGIN,
    QRE_MAX_CANDIDATES,
    QRE_RELEVANCE_THRESHOLD,
    QRE_WEAK_SCORE_THRESHOLD,
)
from qre.engine.discover import (
    derive_shapes,
    filter_offtopic_shapes,
    read_constraints,
    read_five_tuple,
    read_slot_taxonomy,
)
from qre.engine.errors import EngineInputError, GroundingMiss
from qre.engine.extract import DateRequest
from qre.engine.families import (
    DEV_FINANCE_FAMILY,
    DONOR_ROLE_DCID,
    PROP_RECIPIENT,
    RECIPIENT_ROLE_DCID,
)
from qre.engine.families.dev_finance import DEV_FINANCE_RULE
from qre.engine.families.protocol import FamilyRule
from qre.engine.families.registry import REGISTRY, STANDARD_RULE
from qre.engine.graph import EngineGraphClient, Facet
from qre.engine.ground import graphref, graphrefs
from qre.engine.interpret import Recall, recall
from qre.engine.llm import SupportsLLM
from qre.engine.place_role import (
    SEAM_OFF_INFO_CODE,
    SEAM_OFF_WARN_CODE,
    DirectionalRole,
    EntityRoleDraft,
    SubjectRole,
    directional_roles,
)
from qre.engine.retrieve import Materialised, MaterialisedCandidates, NoDataDraft, materialise
from qre.engine.shape import ShapeDraft, build_shape, family_for, shape_draft_from
from qre.models import (
    BindingSet,
    BindingUnbound,
    BindingValue,
    Entity,
    EntityRoleDirectional,
    EntityRoleSubject,
    GraphRef,
    PipelineStep,
    Spec,
    SpecResubmitInput,
    StatusLiteral,
    Warning,
)

logger = logging.getLogger(__name__)

MULTI_RECIPIENT_TRUNCATED = "MULTI_RECIPIENT_TRUNCATED"
RETRIEVAL_SCORE_WEAK = "RETRIEVAL_SCORE_WEAK"


def detect_set_ref(
    *,
    value_dcids: list[str],
    graph: EngineGraphClient,
) -> GraphRef | None:
    """Label a BindingSet with the taxonomy parent it exactly covers, or None.

    Conservative full-children match, one level only: read each member's ->isPartOf
    parents; require every member to have exactly one immediate parent and all equal (P).
    Then read P's complete <-isPartOf children; only when that child set equals the
    member set, ground P to a GraphRef (label from the graph) and return it.

    Any member with != 1 parent, members spanning parents, a partial-children match, or
    an unread parent label → None. Never fabricates, never over-claims partial subsets.
    Scoped to taxonomy (what/how) axes — callers gate on axis before invoking.
    """
    if len(value_dcids) < 2:
        return None
    arcs = graph.node_arcs_batch(value_dcids)
    # Collect the single ->isPartOf parent for each member; any ambiguity → None.
    parent: str | None = None
    for dcid in value_dcids:
        member_arcs = arcs.get(dcid) or {}
        parent_nodes = member_arcs.get("isPartOf", {}).get("nodes", [])
        parent_dcids = [n["dcid"] for n in parent_nodes if "dcid" in n]
        if len(parent_dcids) != 1:
            return None
        p = parent_dcids[0]
        if parent is None:
            parent = p
        elif p != parent:
            return None
    if parent is None:
        return None
    # Full-children check: member set must equal the parent's complete <-isPartOf child set.
    children = set(graph.child_dcids(parent))
    if children != set(value_dcids):
        return None
    label = graph.node_labels_batch([parent]).get(parent)
    if label is None:
        return None
    return GraphRef(dcid=parent, label=label)


def decide_multi_recipient(
    to_dcids: list[str],
    has_constraint_slots: bool,
    *,
    warnings: list[Warning],
) -> tuple[str | None, list[str], list[tuple[str, bool]]]:
    """Gate directional multi-recipient handling by constraint-slot capability.

    Returns ``(recipient_dcid, effective_to_dcids, conditions)``.

    ``conditions`` is an ordered ``(gate_name, matched)`` trace. When
    ``has_constraint_slots`` is True (dev-finance), all recipients are used
    and a DEBUG line is emitted instead of a warning. When False (standard),
    only the first recipient is kept and MULTI_RECIPIENT_TRUNCATED is appended.
    """
    conditions: list[tuple[str, bool]] = [
        ("multi_directional", len(to_dcids) > 1),
        ("has_constraint_slots", has_constraint_slots),
    ]
    if not to_dcids:
        return None, [], conditions
    recipient_dcid = to_dcids[0]
    if len(to_dcids) > 1:
        if has_constraint_slots:
            # Dev-finance path handles all recipients via BindingSet; no warning fires.
            logger.debug("MULTI_RECIPIENT near-miss: %s", conditions)
            return recipient_dcid, to_dcids, conditions
        warnings.append(
            Warning(
                code=MULTI_RECIPIENT_TRUNCATED,
                severity="warn",
                message=f"{len(to_dcids)} directional recipients detected; only 1 used.",
            )
        )
    return recipient_dcid, [recipient_dcid], conditions


# ---------------------------------------------------------------------------
# RegionResult: per-variable resolution seam
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegionResult:
    """Per-variable resolution outcome. One variable → one region.

    The specs tuple carries the pipeline_trace through the embedded Spec(s).
    timing_by_step carries per-step latencies for the response diagnostics.
    """

    variable_text: str
    status: StatusLiteral
    specs: tuple[Spec, ...]     # definite: 1; candidates: 2+; no_data: 0
    no_data_reason: str | None
    warnings: tuple[Warning, ...]
    timing_by_step: dict[str, int]
    earliest_index: int = 0
    nearest_real: tuple[Spec, ...] | None = None  # populated on no_data when relaxation finds data
    llm_usage: dict | None = None  # aggregated token usage for this variable's LLM calls

    @property
    def spec(self) -> Spec:
        """Convenience for the definite path (status == 'definite')."""
        return self.specs[0]


# ---------------------------------------------------------------------------
# Per-variable helper functions
# ---------------------------------------------------------------------------


def _top_dominates(std_shapes: list[ShapeDraft], *, margin: float) -> bool:
    """Return True when the top standard shape's representative-SV cosine score exceeds
    the second's by at least ``margin``.

    The dominance rule ranks shapes in regions.py (not in resolvers) so that shape ranking
    is architecturally separate from materialization logic. The threshold is calibrated
    via QRE_DOMINANCE_MARGIN (config.py).

    Returns False when fewer than two shapes are present (no comparison possible).
    """
    if len(std_shapes) < 2:
        return False
    ranked = sorted(std_shapes, key=lambda s: s.representative_score, reverse=True)
    return ranked[0].representative_score - ranked[1].representative_score >= margin


def standard_bindings_from_arcs(
    *,
    shape: ShapeDraft,
    rep_sv_dcid: str,
) -> list[SlotBindingDraft]:
    """Derive arc-based SlotBindingDrafts for the constraint slots of a standard shape.

    For each constraint slot key on the shape (i.e. slot keys whose property_dcid is not
    None, skipping when/source), reads the representative SV's constraint value from its
    arc facts via read_constraints:
      - present  → SlotBindingDraft(kind="value", value_dcids=[value_dcid])
      - absent   → SlotBindingDraft(kind="absent", value_dcids=[])

    Returns an empty list when the shape carries no constraint slot keys (e.g. Count_Person).
    Called for both the definite and candidates paths; the candidates path calls it once
    per shape with that shape's own representative SV.

    Args:
        shape:        Standard ShapeDraft carrying slot_keys and sv_arc_facts.
        rep_sv_dcid:  The representative SV dcid (first SV in insertion order).

    Returns:
        List of SlotBindingDraft for each constraint slot key, never for when/source.
    """
    # Only act on slots with a constraint property.
    constraint_slots = [
        slot for slot in shape.slot_keys
        if slot.property_dcid is not None and slot.axis not in ("when", "source")
    ]
    if not constraint_slots:
        return []

    # Read the representative SV's confirmed arc facts (no new graph call).
    arc_facts = shape.sv_arc_facts or {}
    rep_arcs = arc_facts.get(rep_sv_dcid, {})
    sv_constraints = read_constraints(rep_arcs)

    result: list[SlotBindingDraft] = []
    for slot in constraint_slots:
        prop = slot.property_dcid
        value = sv_constraints.get(prop)
        if value is not None:
            result.append(
                SlotBindingDraft(
                    axis=slot.axis,
                    property_dcid=prop,
                    kind="value",
                    value_dcids=[value],
                )
            )
        else:
            result.append(
                SlotBindingDraft(
                    axis=slot.axis,
                    property_dcid=prop,
                    kind="absent",
                    value_dcids=[],
                )
            )

    return result


def _materialise_standard_candidates(
    std_shapes: list[ShapeDraft],
    bindings: list[SlotBindingDraft],
    recipient_dcid: str | None,
    donor_dcid: str | None,
    graph: EngineGraphClient,
    date_request: DateRequest | None = None,
) -> tuple[MaterialisedCandidates | NoDataDraft, list[tuple[ShapeDraft, Materialised]]]:
    """Probe each standard shape and return MaterialisedCandidates or NoDataDraft.

    Steps:
      1. Filter shapes with measuredProperty.
      2. Sort by (-sv_arc_facts count, shape_id) — broadest first.
      3. Clamp to QRE_MAX_CANDIDATES.
      4. Dedupe by shape_id (each five-tuple group appears once).
      5. Materialise each shape (probes the representative SV only).
      6. Collect surviving shapes.

    Returns:
        A 2-tuple of:
          - MaterialisedCandidates when >= 1 shapes have observations,
            NoDataDraft("no_observations") when 0 shapes have observations.
          - The (shape, Materialised) pairs in the same order as the
            MaterialisedCandidates.candidates list.
    """
    # Filter out ill-formed shapes (no measuredProperty) before ranking.
    # Shapes without meas_prop_dcid fail Shape model validation; remove them
    # so the rank+clamp cap applies only to valid shapes.
    valid_shapes = [s for s in std_shapes if s.meas_prop_dcid]

    # Rank broadest-first, clamp to QRE_MAX_CANDIDATES
    ranked = sorted(
        valid_shapes,
        key=lambda s: (-(len(s.sv_arc_facts) if s.sv_arc_facts else 0), s.shape_id),
    )
    ranked = ranked[:QRE_MAX_CANDIDATES]

    # Dedupe by shape_id (each five-tuple group appears once).
    seen: set[str] = set()
    deduped: list[ShapeDraft] = []
    for s in ranked:
        if s.shape_id not in seen:
            seen.add(s.shape_id)
            deduped.append(s)

    # Materialise each shape (probes the representative SV only).
    surviving: list[tuple[ShapeDraft, Materialised]] = []
    for shape in deduped:
        result = materialise(
            shape, bindings, recipient_dcid, donor_dcid, graph=graph, date_request=date_request
        )
        if isinstance(result, Materialised):
            surviving.append((shape, result))

    if not surviving:
        return NoDataDraft(reason="no_observations"), []

    mat_list = [m for _, m in surviving]
    return MaterialisedCandidates(candidates=mat_list), surviving


def _build_source_refs(
    facets: list[Facet],
    graph: EngineGraphClient,
) -> list[GraphRef]:
    """Resolve provenance DCIDs from observation facets to labelled GraphRefs.

    Collects distinct provenanceIds (with dc/base/{importName} as a best-effort
    fallback when provenanceId is absent), makes one batched node_labels_batch call,
    and returns GraphRefs only for DCIDs confirmed by the name batch.  Unresolved
    fallback candidates are silently omitted — never fabricated (decision #2).
    """
    # Collect candidate dcids in stable insertion order.
    # provenanceId is authoritative; dc/base/{importName} is best-effort.
    seen: dict[str, bool] = {}  # dcid -> is_authoritative
    for f in facets:
        if f.provenance_id:
            seen[f.provenance_id] = True
        elif f.import_name:
            fallback = f"dc/base/{f.import_name}"
            if fallback not in seen:
                seen[fallback] = False

    if not seen:
        return []

    labels = graph.node_labels_batch(list(seen.keys()))
    return [
        GraphRef(dcid=dcid, label=label)
        for dcid, label in labels.items()
    ]


def _ground_answer(
    shape_draft: ShapeDraft,
    bindings: list[SlotBindingDraft],
    sv_dcids: list[str],
    roles: dict,
    graph: EngineGraphClient,
    facets: list[Facet] | None = None,
) -> tuple:
    """Synchronous grounding work for the answer step.

    Runs blocking graph reads for five-tuple refs, recipient role, slot property/value
    refs, SV refs, and entity type refs. Returns all grounded data so the coroutine
    can assemble model objects without any further blocking calls.
    """
    # Ground five-tuple dcids
    five_tuple_dcids = [
        shape_draft.pop_type_dcid,
        shape_draft.stat_type_dcid,
    ]
    if shape_draft.meas_prop_dcid:
        five_tuple_dcids.append(shape_draft.meas_prop_dcid)
    if shape_draft.meas_qual_dcid:
        five_tuple_dcids.append(shape_draft.meas_qual_dcid)
    if shape_draft.meas_denom_dcid:
        five_tuple_dcids.append(shape_draft.meas_denom_dcid)

    five_tuple_ref_list = graphrefs(five_tuple_dcids, graph=graph)
    five_tuple_refs: dict[str, GraphRef] = {r.dcid: r for r in five_tuple_ref_list}

    binding_by_prop: dict[str | None, SlotBindingDraft] = {
        b.property_dcid: b for b in bindings
    }

    # Ground the directional role nodes. Recipient is constraint-sourced
    # (DevelopmentFinanceRecipient); donor is observation-sourced (observationAbout).
    role_refs: dict[str, GraphRef] = {}
    for role_dcid in (RECIPIENT_ROLE_DCID, DONOR_ROLE_DCID):
        try:
            role_refs[role_dcid] = graphref(role_dcid, graph=graph)
        except GroundingMiss:
            role_refs[role_dcid] = GraphRef(dcid=role_dcid, label=role_dcid)

    # Build grounded Slots
    slots = []
    slot_key_models = []
    for slot_draft in shape_draft.slot_keys:
        prop_dcid = slot_draft.property_dcid

        prop_ref: GraphRef | None = None
        if prop_dcid:
            try:
                prop_ref = graphref(prop_dcid, graph=graph)
            except GroundingMiss:
                prop_ref = GraphRef(dcid=prop_dcid, label=prop_dcid)

        b_draft = binding_by_prop.get(prop_dcid)

        grounded_vals: list[GraphRef] = []
        if b_draft and b_draft.kind in ("value", "set"):
            grounded_vals = graphrefs(b_draft.value_dcids, graph=graph)

        # For a what/how set with 2+ grounded values, check whether they are the
        # complete <-isPartOf children of a shared taxonomy parent (decision §0.3).
        # The where-axis is gated out: isPartOf also models geographic containment.
        slot_set_ref: GraphRef | None = None
        if (
            b_draft is not None
            and b_draft.kind == "set"
            and slot_draft.axis in ("what", "how")
            and len(grounded_vals) >= 2
        ):
            slot_set_ref = detect_set_ref(
                value_dcids=[gv.dcid for gv in grounded_vals], graph=graph
            )

        slot = build_slot(
            slot_draft,
            b_draft,
            grounded_vals,
            property_ref=prop_ref,
            set_ref=slot_set_ref,
        )
        slots.append(slot)
        slot_key_models.append(slot.key)

    # Ground SV dcids
    sv_refs = graphrefs(sv_dcids, graph=graph)

    # Ground entity refs and types
    entity_objects_data = []
    for dcid, role_draft in roles.items():
        try:
            entity_ref = graphref(dcid, graph=graph)
        except GroundingMiss:
            continue

        entity_type_ref: GraphRef | None = None
        node_type = graph.node_type(dcid)
        if node_type:
            try:
                entity_type_ref = graphref(node_type, graph=graph)
            except GroundingMiss:
                entity_type_ref = GraphRef(dcid=node_type, label=node_type)

        entity_objects_data.append((role_draft, entity_ref, entity_type_ref))

    source_refs = _build_source_refs(facets or [], graph)

    return (
        five_tuple_refs,
        role_refs,
        slots,
        slot_key_models,
        sv_refs,
        entity_objects_data,
        source_refs,
    )


def _build_entity(
    role_draft: EntityRoleDraft,
    entity_ref: GraphRef,
    entity_type_ref: GraphRef | None,
    role_refs: dict[str, GraphRef],
) -> Entity:
    """Build a grounded Entity from a role draft."""
    role = role_draft.role
    if isinstance(role, DirectionalRole) and role.kind == "directional":
        # The role GraphRef audits how the directional role is sourced: the recipient
        # from the DevelopmentFinanceRecipient constraint, the donor from
        # observationAbout. render reads entity.ref.label, not this ref.
        entity_role = EntityRoleDirectional(
            kind="directional",
            role=role_refs.get(role.role_dcid)
            or GraphRef(dcid=role.role_dcid, label=role.role_dcid),
            direction=role.direction,
        )
    else:
        entity_role = EntityRoleSubject()

    return Entity(ref=entity_ref, entity_type=entity_type_ref, role=entity_role)


# ---------------------------------------------------------------------------
# _suggest_nearest_real: axis-relaxation probe for the no_data path
# ---------------------------------------------------------------------------


def _suggest_nearest_real(
    *,
    graph: EngineGraphClient,
    shape_draft: ShapeDraft,
    bindings: list[SlotBindingDraft],
    roles: dict,
    recipient_dcid: str | None,
    donor_dcid: str | None,
    date_request: DateRequest | None,
    n_max: int = 2,
) -> list[Spec]:
    """Relax one constraint binding at a time; return up to n_max grounded Specs.

    Called synchronously on a no_observations path. Each iteration replaces one
    non-trivial (value/set) binding with "unbound" and probes graph_confirm_resolve.
    Only confirmed graph reads are used — no fabricated strings.

    Returns an empty list when no relaxation yields data or on any error.
    """
    from qre.engine.discover import (
        graph_confirm_resolve,  # noqa: PLC0415 (avoid circular at module level)
    )
    from qre.engine.retrieve import Materialised  # noqa: PLC0415

    suggestions: list[Spec] = []

    for i, binding in enumerate(bindings):
        if len(suggestions) >= n_max:
            break
        # Only relax value/set bindings on constraint axes (skip when/source and unbound/absent).
        if binding.kind not in ("value", "set"):
            continue
        if binding.axis in ("when", "source"):
            continue

        # Build relaxed binding list: replace binding[i] with unbound.
        relaxed = list(bindings)
        relaxed[i] = SlotBindingDraft(
            axis=binding.axis,
            property_dcid=binding.property_dcid,
            kind="unbound",
            value_dcids=[],
        )

        # DELIBERATE, NARROW fail-loud exception: this is the OPTIONAL suggestion
        # probe on an already-decided no_data path. The relaxed graph probe
        # (graph_confirm_resolve) can raise GraphInfraError, and the downstream
        # spec build can raise on invalid intermediate state. Per this helper's
        # contract ("returns an empty list on any error"), ANY failure here must
        # yield no suggestion rather than convert a valid no_data response into a
        # 503. This guard applies ONLY to the optional probe — it does NOT relax
        # fail-loud on the primary resolution path.
        try:
            mat = graph_confirm_resolve(
                shape=shape_draft,
                bindings=relaxed,
                recipient_dcid=recipient_dcid,
                donor_dcid=donor_dcid,
                graph=graph,
                date_request=date_request,
            )
            if not isinstance(mat, Materialised):
                continue

            ground = _ground_answer(
                shape_draft, relaxed, mat.sv_dcids, roles, graph, mat.facets
            )

            (ft_refs, role_refs, slots, slot_key_models, sv_refs, entity_data, source_refs) = ground
            if not sv_refs:
                continue

            shape_model = build_shape_model(
                shape_draft, slot_key_models, ft_refs, member_count=len(sv_refs)
            )
            stat_vars_list = build_stat_vars(
                sv_refs,
                shape_draft.shape_id,
                slots,
                facets_by_sv=mat.facets_by_sv,
                recipient_confirmed=mat.recipient_confirmed,
            )
            entities_list = [
                _build_entity(rd, er, etr, role_refs) for rd, er, etr in entity_data
            ]
            slots = bind_when_slot(slots, window=mat.coverage.window)
            spec = build_spec(
                shape=shape_model,
                slots=slots,
                stat_vars=stat_vars_list,
                entities=entities_list,
                coverage=mat.coverage,
                pipeline_trace=[],
                resolved_sources=source_refs,
            )
        except Exception:  # noqa: BLE001 — optional probe: any error yields no suggestion
            continue

        suggestions.append(spec)

    return suggestions


# ---------------------------------------------------------------------------
# resolve_variable: per-variable core pipeline (recall → answer)
# ---------------------------------------------------------------------------


async def resolve_variable(
    variable: str,
    *,
    entities: list[str],
    date_request: DateRequest | None,
    detect_query: str,
    role_query: str,
    pac: bool,
    graph: EngineGraphClient,
    llm: SupportsLLM,
    base_steps: list[PipelineStep],
    base_timing: dict[str, int],
    pre_resolved: dict[str, str] | None = None,
) -> RegionResult:
    """Run the per-variable pipeline from recall through answer.

    Args:
        variable:     The variable text to resolve.
        entities:     Extracted entity surface forms from the shared extraction step.
        date_request: Date window from the extraction, shared across all variables.
        detect_query: Query string for recall/detect; equals the raw query for N==1
                      and the bare variable for N>=2.
        role_query:   Query string for directional role detection; always the full raw
                      query so that "from"/"to" prepositions are visible to every leg
                      regardless of N (the preposition signal is query-level).
        pac:          place_as_constraint seam flag.
        graph:        Graph client (shared, thread-safe).
        llm:          LLM wrapper (shared, stateless per call).
        base_steps:   Pipeline steps from the shared extract stage ([extract_step]).
        base_timing:  Timing dict from the shared extract stage ({"extract": ms}).
        pre_resolved: Optional map of already-resolved entity names to dcids. When
                      provided, these entities skip the per-variable resolve_entity call.
                      Reduces graph round-trips when multiple variables share the same
                      entity list.

    Returns:
        A RegionResult with status "definite", "candidates", or "no_data".
    """
    pipeline_steps: list[PipelineStep] = list(base_steps)
    timing: dict[str, int] = dict(base_timing)
    warnings: list[Warning] = []

    _var_usage: dict | None = None  # accumulates LLM token usage for this variable's pipeline calls

    def _no_data(reason: str, *, llm_usage: dict | None = None) -> RegionResult:
        return RegionResult(
            variable_text=variable,
            status="no_data",
            specs=(),
            no_data_reason=reason,
            warnings=tuple(warnings),
            timing_by_step=dict(timing),
            llm_usage=llm_usage,
        )

    # --- Step: recall ---
    t0 = now_ms()
    rcl: Recall = await recall(
        variable, entities, graph=graph, raw_query=detect_query, pre_resolved=pre_resolved
    )
    timing["recall"] = now_ms() - t0
    pipeline_steps.append(make_pipeline_step("recall", ran=True, ms=timing["recall"]))

    # Capture the count before derive_shapes applies relevance filtering.
    n_recalled: int = len(rcl.candidate_svs)

    # --- Step: shape ---
    # Graph-derived shape discovery: confirm each candidate SV via node_arcs,
    # group by five-tuple, build a ShapeDraft per group.
    # The blocking graph reads run in a worker thread.
    t0 = now_ms()
    # Build the sv_scores map for derive_shapes. If detect returns a dcid twice
    # (not expected in practice), last score wins.
    sv_scores_map = dict(zip(rcl.candidate_svs, rcl.candidate_sv_scores))
    shapes_found: list[ShapeDraft] = await asyncio.to_thread(
        derive_shapes,
        confirmed_svs=rcl.candidate_svs,
        graph=graph,
        sv_scores=sv_scores_map,
    )

    # When derive_shapes finds no confirmed SVs (all candidates fail node_arcs or detect
    # returned nothing), fall back to family recognition from the raw candidate list so
    # the dev-finance materialise path (which uses construct_sv_dcid, not confirmed SVs)
    # still runs.  When the raw list is also empty or unrecognised, dead-end.
    if not shapes_found:
        family = family_for(rcl.candidate_svs)
        if family is None:
            # No confirmed shapes and no recognised family (e.g. nd-03: detect returns
            # nothing for an unknown variable like "left-handedness rate").
            timing["shape"] = now_ms() - t0
            pipeline_steps.append(make_pipeline_step("shape", ran=True, ms=timing["shape"]))
            for step in ("bind", "materialise", "answer"):
                pipeline_steps.append(make_pipeline_step(step, ran=False))
            return _no_data("variable_not_resolved")
        # Fallback: build the shape from the family's hardcoded five-tuple.
        # The dev-finance materialise path (construct_sv_dcid) does not need confirmed SVs.
        # Stamp DEV_FINANCE_RULE so read_slot_taxonomy uses the seed for this shape:
        # the taxonomy must come from the seed for ALL dev-finance shapes, derived or fallback.
        fallback_shape = build_shape(family)
        shapes_found = [dataclasses.replace(fallback_shape, family_rule=DEV_FINANCE_RULE)]

    # When derive_shapes produces shapes from multiple families, sort by descending
    # resolver score so the family that best matches the query leads.  Registry order
    # breaks ties (dev-finance is registered before standard, so equal-score shapes
    # keep dev-finance first, which is correct for mixed-recall queries).
    #
    # The score() method on each family's resolver implements the CRS_DAC disambiguation:
    # entity-specific SV suffix → higher score, high CRS_DAC density → moderate score.
    # _StandardResolver has no score() method; the getattr fallback returns 0.
    if len(shapes_found) > 1:
        resolved_dcids = set(rcl.resolved_entity_names.values())
        resolved_short = (
            {dcid.split("/")[-1] for dcid in resolved_dcids if dcid} | resolved_dcids
        )
        # Sort key: (-score, -registry_index) — highest score leads; for equal
        # scores, higher registry_index wins (STANDARD_RULE is last in REGISTRY,
        # so it has the highest index and therefore leads when dev-finance scores 0).
        # FamilyRule is not hashable (axis_pins is a dict), so use id() for lookup.
        _registry_index = {id(rule): i for i, rule in enumerate(REGISTRY)}
        shapes_found = sorted(
            shapes_found,
            key=lambda s: (
                -getattr(s.family_rule.resolver, "score", lambda **_: 0)(
                    candidate_svs=rcl.candidate_svs,
                    resolved_short=resolved_short,
                ),
                -_registry_index.get(id(s.family_rule), -1),
            ) if s.family_rule else (0, 0),
        )

    # Drop STANDARD shapes that lack measuredProperty — they fail Shape model
    # validation in build_shape_model (measured_property is a required GraphRef).
    # Non-standard families (e.g. dev-finance) are never filtered here; their
    # shapes always carry meas_prop and go through a separate resolver path.
    shapes_found = [
        s for s in shapes_found
        if s.family_rule is not STANDARD_RULE or s.meas_prop_dcid
    ]
    if not shapes_found:
        for step in ("bind", "materialise", "answer"):
            pipeline_steps.append(make_pipeline_step(step, ran=False))
        return _no_data("variable_not_resolved")

    # Candidate-noise filter: drop STANDARD shapes whose representative SV's display
    # label shares no content-word token with the variable phrase.  Applied only to
    # standard shapes; dev-finance shapes pass through untouched.
    shapes_found = filter_offtopic_shapes(shapes_found, variable=variable)
    if not shapes_found:
        for step in ("bind", "materialise", "answer"):
            pipeline_steps.append(make_pipeline_step(step, ran=False))
        return _no_data("variable_not_resolved")

    # Detect the candidates case: multiple distinct standard shapes.  Dev-finance is
    # always monomorphic (one shape per query); only standard shapes trigger candidates.
    # The dominance rule routes standard to definite when the top shape's representative-SV
    # score exceeds the second's by QRE_DOMINANCE_MARGIN.
    std_shapes_all = [s for s in shapes_found if s.family_rule is STANDARD_RULE]
    _is_candidates_path = (
        len(std_shapes_all) >= 2
        and shapes_found[0].family_rule is STANDARD_RULE
        and not _top_dominates(std_shapes_all, margin=QRE_DOMINANCE_MARGIN)
    )

    shape_draft: ShapeDraft = shapes_found[0]
    # When dominance fires (standard leads, ≥2 standard shapes, dominance → definite),
    # use the cosine-top standard shape rather than the registry-index-sorted shapes_found[0].
    # This guard prevents a dev-finance-led query from being hijacked if multiple standard
    # noise shapes are also present.
    if (
        not _is_candidates_path
        and shapes_found[0].family_rule is STANDARD_RULE
        and len(std_shapes_all) >= 2
    ):
        shape_draft = max(std_shapes_all, key=lambda s: s.representative_score)
    timing["shape"] = now_ms() - t0
    pipeline_steps.append(make_pipeline_step("shape", ran=True, ms=timing["shape"]))

    # Warn when the top shape's representative cosine is above the relevance floor
    # but below the weak-score threshold. The != 1.0 sentinel guard prevents false
    # fires on the dev-finance fallback / offline-fixture default (shape.py:71).
    # Not emitted on the candidates path (no single winning score there).
    if (
        not _is_candidates_path
        and shape_draft.representative_score != 1.0  # noqa: PLR2004
        and QRE_RELEVANCE_THRESHOLD < shape_draft.representative_score < QRE_WEAK_SCORE_THRESHOLD
    ):
        warnings.append(
            Warning(
                code=RETRIEVAL_SCORE_WEAK,
                severity="info",
                message=(
                    f"top shape cosine {shape_draft.representative_score:.2f} is weak"
                    f" (< {QRE_WEAK_SCORE_THRESHOLD})"
                ),
            )
        )

    # Any named entity that failed to resolve fires entity_not_resolved to ensure
    # the engine never silently answers a different query.
    distinct_extracted = len(set(entities))
    if entities and len(rcl.resolved_entity_names) < distinct_extracted:
        for step in ("bind", "materialise", "answer"):
            pipeline_steps.append(make_pipeline_step(step, ran=False))
        return _no_data("entity_not_resolved")

    # --- Place role classification ---
    resolved_pairs: list[tuple[str, str | None]] = [
        (dcid, surface)
        for surface, dcid in rcl.resolved_entity_names.items()
    ]

    # Fetch canonical graph labels for all resolved entities; these provide a third anchor
    # in direction detection beyond the surface text and DCID slug. node_labels_batch
    # reads cached hits (populated during shape discovery), so this adds no new round-trips.
    _entity_dcids = [dcid for dcid, _ in resolved_pairs]
    _label_batch = graph.node_labels_batch(_entity_dcids) if _entity_dcids else {}
    canonical_names: dict[str, str | None] = {
        dcid: _label_batch.get(dcid) for dcid in _entity_dcids
    }

    # Find the directional recipient entity for SV construction (always with pac=True);
    # the seam flag affects only how roles are presented in the response.
    # role_query is always the full raw query so that "from"/"to" prepositions are
    # visible across all conjunction legs (detect_query may be a bare variable for N>=2).
    roles_for_sv, _, directional_detected_sv = directional_roles(
        role_query,
        resolved_pairs,
        place_as_constraint=True,
        recipient_role_dcid=RECIPIENT_ROLE_DCID,
        donor_role_dcid=DONOR_ROLE_DCID,
        canonical_names=canonical_names,
    )

    # When seam=ON (pac=True) the two role calls would be identical; skip the redundant second.
    if pac:
        roles = roles_for_sv
        seam_off = False
        directional_detected = False
    else:
        roles, seam_off, directional_detected = directional_roles(
            role_query,
            resolved_pairs,
            place_as_constraint=False,
            recipient_role_dcid=RECIPIENT_ROLE_DCID,
            donor_role_dcid=DONOR_ROLE_DCID,
            canonical_names=canonical_names,
        )

    # Emit seam warnings
    if seam_off:
        warnings.append(
            Warning(
                code=SEAM_OFF_INFO_CODE,
                severity="info",
                message="place_as_constraint is OFF; all entities treated as subjects.",
            )
        )
    if seam_off and directional_detected:
        warnings.append(
            Warning(
                code=SEAM_OFF_WARN_CODE,
                severity="warn",
                message=(
                    "Directional prepositions detected in query but place-as-constraint "
                    "seam is OFF; role assignment disabled."
                ),
            )
        )

    # Find recipient and donor dcids from the seam=ON roles (always, for SV construction)
    recipient_dcid: str | None = None
    donor_dcid: str | None = None
    to_dcids: list[str] = []
    for dcid, role_draft in roles_for_sv.items():
        if isinstance(role_draft.role, DirectionalRole):
            if role_draft.role.direction == "to":
                to_dcids.append(dcid)
            elif role_draft.role.direction == "from":
                donor_dcid = dcid
    # First element is the scalar stability anchor for _construct_resolve and the
    # standard else-branch; spec_id.py sorts BindingSet dcids so the hash is
    # order-independent regardless of detection order.
    if to_dcids:
        recipient_dcid = to_dcids[0]

    # Treat a bare entity as the recipient only when no directional signal was detected.
    # If directional_detected_sv is True, at least one entity had a "from" or "to"
    # preposition; then recipient_dcid is already set (for "to") or intentionally None
    # (for lone "from"), and this fallback must not fire.
    if (
        recipient_dcid is None
        and not directional_detected_sv
        and len(rcl.resolved_entity_names) == 1
    ):
        recipient_dcid = next(iter(rcl.resolved_entity_names.values()))
    elif recipient_dcid is None and not directional_detected_sv and rcl.resolved_entity_names:
        # Multiple bare entities (no directional prepositions): use the last entity (heuristic)
        recipient_dcid = next(reversed(list(rcl.resolved_entity_names.values())))
        # This warning fires for bare-entity multi-recipient only. Directional multi-recipient
        # queries route through a BindingSet covering all recipients and do not reach this path.
        warnings.append(
            Warning(
                code=MULTI_RECIPIENT_TRUNCATED,
                severity="warn",
                message=(
                    f"{len(rcl.resolved_entity_names)} bare entities detected; "
                    "only 1 used as recipient (heuristic)."
                ),
            )
        )

    # --- Build slot taxonomy for bind ---
    # Use the per-shape taxonomy stamped by discover.read_slot_taxonomy.
    # For dev-finance this is the hand-verified seed; for standard it is the
    # observed-union from the arc facts carried on the ShapeDraft.
    # The where/recipient slot is injected here after deterministic entity resolution.
    slot_taxonomy: dict[str, list[str]] = {}
    if shape_draft.slot_taxonomy is not None:
        slot_taxonomy.update(shape_draft.slot_taxonomy)
    else:
        # Fallback: derive from the shape (legacy path).
        slot_taxonomy = read_slot_taxonomy(shape_draft=shape_draft, graph=graph)

    # Standard shapes skip the LLM bind call entirely; they probe observations directly
    # from recipient_dcid without binding.  Dev-finance shapes always bind.
    _is_standard = shape_draft.family_rule is STANDARD_RULE
    _has_constraint_slots = bool(slot_taxonomy) and not _is_standard

    # Gate directional multi-recipient handling: dev-finance can carry all recipients in a
    # BindingSet; standard truncates to one. decide_multi_recipient encapsulates the
    # condition chain and emits a near-miss DEBUG trace when dev-finance suppresses the warning.
    _recipient_direct, effective_to_dcids, _mr_conditions = decide_multi_recipient(
        to_dcids, _has_constraint_slots, warnings=warnings
    )

    if _has_constraint_slots:
        # Inject the deterministically-resolved recipient into the where slot.
        # This mirrors the existing dev-finance behaviour: the LLM is shown the
        # recipient but the binding is overwritten below regardless of what it returns.
        where_key = f"where:{PROP_RECIPIENT}"
        slot_taxonomy[where_key] = [recipient_dcid] if recipient_dcid else []

        # --- Step: bind ---
        t0 = now_ms()
        bind_result: _BindOutput
        _bind_usage: dict | None
        bind_result, _bind_usage = await bind(variable, slot_taxonomy, llm=llm)
        _var_usage = _bind_usage  # record token usage for this variable's bind call
        timing["bind"] = now_ms() - t0
        pipeline_steps.append(make_pipeline_step("bind", ran=True, ms=timing["bind"]))

        # When the LLM signals the variable is completely off-topic for this taxonomy,
        # return no_data immediately. Never fail-open on an all-unbound response.
        if bind_result.ask:
            for step in ("materialise", "answer"):
                pipeline_steps.append(make_pipeline_step(step, ran=False))
            return _no_data("variable_not_resolved", llm_usage=_var_usage)

        bindings: list[SlotBindingDraft] = bind_result.bindings

        # The recipient is resolved deterministically (entity resolution + directional
        # detection), so override the LLM binding with the resolved recipient dcid
        # to ensure the where slot is always present in the spec.
        if recipient_dcid:
            where_binding = next((b for b in bindings if b.axis == "where"), None)
            if where_binding is None:
                where_binding = SlotBindingDraft(
                    axis="where", property_dcid=PROP_RECIPIENT, kind="value", value_dcids=[]
                )
                bindings.append(where_binding)
            if len(effective_to_dcids) > 1:
                where_binding.kind = "set"
                where_binding.value_dcids = list(effective_to_dcids)
            else:
                where_binding.kind = "value"
                where_binding.value_dcids = [recipient_dcid]
    else:
        # No constraint slots (from slot_taxonomy): skip LLM bind entirely (standard family
        # with bare count SV, etc.).  Build a minimal where-only binding from the resolved
        # entity so the grounding stage has a recipient to display.  No property_dcid for
        # the entity-only where slot (matches the SlotKeyDraft(property_dcid=None) pattern).
        # decide_multi_recipient already emitted MULTI_RECIPIENT_TRUNCATED if applicable;
        # no duplicate warning needed here.
        pipeline_steps.append(make_pipeline_step("bind", ran=False))
        bindings = []
        if recipient_dcid:
            bindings.append(
                SlotBindingDraft(
                    axis="where",
                    property_dcid=None,
                    kind="value",
                    value_dcids=[recipient_dcid],
                )
            )
        # Derive arc-based constraint bindings for standard shapes from the representative
        # SV's arc facts (no LLM call; no new graph reads).
        if shape_draft.sv_arc_facts:
            rep_sv = next(iter(shape_draft.sv_arc_facts))
            bindings.extend(
                standard_bindings_from_arcs(shape=shape_draft, rep_sv_dcid=rep_sv)
            )

    # Denominator check for per-capita queries
    if "per capita" in variable.lower() and shape_draft.meas_denom_dcid is None:
        pipeline_steps.append(make_pipeline_step("materialise", ran=False))
        pipeline_steps.append(make_pipeline_step("answer", ran=False))
        return _no_data("denominator_not_available")

    # --- Step: materialise ---
    # For multiple standard shapes (candidates path): rank+clamp by member_count,
    # probe each shape's representative SV, and collect into MaterialisedCandidates.
    # For a single shape (definite path): delegate to the single-shape materialise path.
    t0 = now_ms()
    # shape_mat_pairs accumulates (shape_draft, Materialised) for the candidates path.
    shape_mat_pairs: list[tuple[ShapeDraft, "Materialised"]] = []

    if _is_candidates_path:
        mat_result, shape_mat_pairs = await asyncio.to_thread(
            _materialise_standard_candidates,
            std_shapes_all,
            bindings,
            recipient_dcid,
            donor_dcid,
            graph,
            date_request,
        )
    else:
        mat_result = await asyncio.to_thread(
            materialise,
            shape_draft,
            bindings,
            recipient_dcid,
            donor_dcid,
            graph=graph,
            date_request=date_request,
        )

    timing["materialise"] = now_ms() - t0
    pipeline_steps.append(make_pipeline_step("materialise", ran=True, ms=timing["materialise"]))

    if isinstance(mat_result, NoDataDraft):
        pipeline_steps.append(make_pipeline_step("answer", ran=False))
        # Relax constraint bindings to suggest nearby data when observations are missing.
        # Only probe the definite path (single shape); candidates path has multiple shapes.
        nearest_real: tuple[Spec, ...] | None = None
        if not _is_candidates_path and mat_result.reason == "no_observations":
            near_specs = await asyncio.to_thread(
                _suggest_nearest_real,
                graph=graph,
                shape_draft=shape_draft,
                bindings=bindings,
                roles=roles,
                recipient_dcid=recipient_dcid,
                donor_dcid=donor_dcid,
                date_request=date_request,
            )
            if near_specs:
                nearest_real = tuple(near_specs)
        return RegionResult(
            variable_text=variable,
            status="no_data",
            specs=(),
            no_data_reason=mat_result.reason,
            warnings=tuple(warnings),
            timing_by_step=dict(timing),
            nearest_real=nearest_real,
            llm_usage=_var_usage,
        )

    # --- Step: answer (ground everything and assemble Spec) ---
    t0 = now_ms()

    if isinstance(mat_result, MaterialisedCandidates):
        # Candidates path: ground the representative SV for each surviving shape,
        # build a Spec per shape, then assemble into a CandidatesResponse.
        specs: list[Spec] = []
        for cand_shape, cand_mat in shape_mat_pairs:
            # Compute per-shape arc-derived constraint bindings so each candidate
            # spec carries the correct constraint values for its own representative SV.
            cand_rep_sv = next(iter(cand_shape.sv_arc_facts or {}), None)
            cand_arc_bindings = (
                standard_bindings_from_arcs(shape=cand_shape, rep_sv_dcid=cand_rep_sv)
                if cand_rep_sv
                else []
            )
            cand_bindings = list(bindings) + cand_arc_bindings
            cand_ground = _ground_answer(
                cand_shape,
                cand_bindings,
                cand_mat.sv_dcids,
                roles,
                graph,
                cand_mat.facets,
            )
            (
                ft_refs,
                role_refs,
                cand_slots,
                cand_slot_key_models,
                cand_sv_refs,
                cand_entity_data,
                cand_source_refs,
            ) = cand_ground

            # member_count for the Shape model reflects the shape group size
            # (number of confirmed SVs in this five-tuple group from derive_shapes),
            # not the count of grounded SVs in this spec (which is 1: the representative).
            # This preserves the broadest-first semantics of the spec_id sort key.
            cand_member_count = len(cand_shape.sv_arc_facts) if cand_shape.sv_arc_facts else 1
            cand_shape_model = build_shape_model(
                cand_shape,
                cand_slot_key_models,
                ft_refs,
                member_count=cand_member_count,
            )
            cand_stat_vars = build_stat_vars(
                cand_sv_refs,
                cand_shape.shape_id,
                cand_slots,
                facets_by_sv=cand_mat.facets_by_sv,
                recipient_confirmed=cand_mat.recipient_confirmed,
            )
            cand_entities = [
                _build_entity(rd, er, etr, role_refs)
                for rd, er, etr in cand_entity_data
            ]
            cand_slots = bind_when_slot(cand_slots, window=cand_mat.coverage.window)
            cand_spec = build_spec(
                shape=cand_shape_model,
                slots=cand_slots,
                stat_vars=cand_stat_vars,
                entities=cand_entities,
                coverage=cand_mat.coverage,
                pipeline_trace=pipeline_steps,
                variable_text=variable,
                resolved_sources=cand_source_refs,
                n_recalled=n_recalled,
            )
            specs.append(cand_spec)

        # Dedupe by spec_id (shapes with identical bindings collapse to one spec)
        seen_spec_ids: set[str] = set()
        unique_specs: list[Spec] = []
        for s in specs:
            if s.spec_id not in seen_spec_ids:
                seen_spec_ids.add(s.spec_id)
                unique_specs.append(s)

        timing["answer"] = now_ms() - t0
        pipeline_steps.append(make_pipeline_step("answer", ran=True, ms=timing["answer"]))

        if len(unique_specs) == 0:
            return _no_data("no_observations", llm_usage=_var_usage)
        if len(unique_specs) == 1:
            # Single surviving candidate collapses to a definite region.
            return RegionResult(
                variable_text=variable,
                status="definite",
                specs=(unique_specs[0],),
                no_data_reason=None,
                warnings=tuple(warnings),
                timing_by_step=dict(timing),
                llm_usage=_var_usage,
            )
        return RegionResult(
            variable_text=variable,
            status="candidates",
            specs=tuple(unique_specs),
            no_data_reason=None,
            warnings=tuple(warnings),
            timing_by_step=dict(timing),
            llm_usage=_var_usage,
        )

    # Definite path: single Materialised result.
    grounding_result = await asyncio.to_thread(
        _ground_answer,
        shape_draft,
        bindings,
        mat_result.sv_dcids,
        roles,
        graph,
        mat_result.facets,
    )

    (
        five_tuple_refs,
        role_refs,
        slots,
        slot_key_models,
        sv_refs,
        entity_objects_data,
        source_refs,
    ) = grounding_result

    # Build the Shape model
    shape_model = build_shape_model(
        shape_draft,
        slot_key_models,
        five_tuple_refs,
        member_count=len(sv_refs),
    )

    # Build StatVars
    stat_vars = build_stat_vars(
        sv_refs,
        shape_draft.shape_id,
        slots,
        facets_by_sv=mat_result.facets_by_sv,
        recipient_confirmed=mat_result.recipient_confirmed,
    )

    # Assemble Entity objects from grounded data
    entity_objects: list[Entity] = [
        _build_entity(role_draft, entity_ref, entity_type_ref, role_refs)
        for role_draft, entity_ref, entity_type_ref in entity_objects_data
    ]

    timing["answer"] = now_ms() - t0
    pipeline_steps.append(make_pipeline_step("answer", ran=True, ms=timing["answer"]))

    # Bind the when-slot to the extracted window before assembling the Spec
    slots = bind_when_slot(slots, window=mat_result.coverage.window)

    # Assemble the Spec
    spec = build_spec(
        shape=shape_model,
        slots=slots,
        stat_vars=stat_vars,
        entities=entity_objects,
        coverage=mat_result.coverage,
        pipeline_trace=pipeline_steps,
        variable_text=variable,
        resolved_sources=source_refs,
        n_recalled=n_recalled,
    )

    return RegionResult(
        variable_text=variable,
        status="definite",
        specs=(spec,),
        no_data_reason=None,
        warnings=tuple(warnings),
        timing_by_step=dict(timing),
        llm_usage=_var_usage,
    )


# ---------------------------------------------------------------------------
# resolve_spec_resubmit: Path C entry — named refine + standard promote
# ---------------------------------------------------------------------------

# NOTE: materialise → _ground_answer → build_spec is fully synchronous;
# no async def in the chain.  The asyncio.to_thread wrap in core.py is therefore
# safe and non-blocking on the event loop.


def _slots_to_binding_drafts(slots: list) -> list[SlotBindingDraft]:
    """Convert posted Slot objects to SlotBindingDraft for the pipeline.

    Posted labels are discarded; only the dcids are carried forward.
    time_window and literal bindings have no ref dcid and map to unbound.

    Note: SlotKey.property is a GraphRef | None (not a plain string); extract
    the dcid explicitly.
    """
    result: list[SlotBindingDraft] = []
    for slot in slots:
        axis = slot.key.axis
        prop_ref = slot.key.property
        prop = prop_ref.dcid if prop_ref is not None else None
        binding = slot.binding
        if isinstance(binding, BindingValue):
            ref = binding.value.ref
            if ref is not None:
                result.append(SlotBindingDraft(
                    axis=axis, property_dcid=prop, kind="value", value_dcids=[ref.dcid]
                ))
            else:
                result.append(SlotBindingDraft(
                    axis=axis, property_dcid=prop, kind="unbound", value_dcids=[]
                ))
        elif isinstance(binding, BindingSet):
            dcids = [v.ref.dcid for v in binding.values if v.ref is not None]
            result.append(SlotBindingDraft(
                axis=axis, property_dcid=prop, kind="set", value_dcids=dcids
            ))
        elif isinstance(binding, BindingUnbound):
            result.append(SlotBindingDraft(
                axis=axis, property_dcid=prop, kind="unbound", value_dcids=[]
            ))
        else:  # BindingAbsent
            result.append(SlotBindingDraft(
                axis=axis, property_dcid=prop, kind="absent", value_dcids=[]
            ))
    return result


def _date_request_from_slots(slots: list) -> DateRequest | None:
    """Derive DateRequest from the when-slot binding when present."""
    for slot in slots:
        if slot.key.axis == "when" and isinstance(slot.binding, BindingValue):
            tw = slot.binding.value.time_window
            if tw is not None:
                return DateRequest(window=tw, latest=False)
    return None


def resolve_spec_resubmit(
    *,
    inp: SpecResubmitInput,
    rule: FamilyRule | None,
    graph: EngineGraphClient,
) -> RegionResult:
    """Resolve a spec_resubmit input, bypassing extract/recall/shape/bind.

    Branches on rule:
      - Named family (rule not None, e.g. DEV_FINANCE_RULE): refine supported.
        Reconstructs SVs from the (possibly edited) slots via construct_sv_dcid.
      - Standard (rule is None): promote-only.
        Re-reads posted SV arcs, regenerates shape_id, guards against edited
        bindings, then materialises the unchanged candidate.

    In both paths:
      - extract step is marked ran=False in the pipeline trace.
      - All GraphRef labels are read from the graph (decision #2); posted labels
        are discarded.
      - Absent graph nodes → no_data, never fabricated refs.

    Raises:
        EngineInputError: For unroutable inputs (unknown shape, mismatch, missing
            stat_var_dcids for standard) or promote-only violations (code="promote_only").
    """
    extract_step = make_pipeline_step("extract", ran=False)
    pipeline_steps: list[PipelineStep] = [extract_step]

    if rule is not None:
        return _resolve_named_family_resubmit(inp, rule, graph, pipeline_steps)
    return _resolve_standard_promote(inp, graph, pipeline_steps)


def _no_data_region(variable_text: str) -> RegionResult:
    """Minimal no_data region for absent-graph-node outcomes."""
    return RegionResult(
        variable_text=variable_text,
        status="no_data",
        specs=(),
        no_data_reason="variable_not_resolved",
        warnings=(),
        timing_by_step={},
    )


def _resolve_named_family_resubmit(
    inp: SpecResubmitInput,
    rule: FamilyRule,
    graph: EngineGraphClient,
    pipeline_steps: list[PipelineStep],
) -> RegionResult:
    """Named-family path (dev-finance): refine supported via construct_sv_dcid."""
    variable_text = inp.shape_id  # best label available without extraction

    # Build the canonical ShapeDraft for this family (mirrors the fallback path
    # in resolve_variable).
    shape_draft = dataclasses.replace(
        build_shape(DEV_FINANCE_FAMILY), family_rule=rule
    )

    # Collect all posted dcids that carry graph refs; re-read their labels.
    # Any dcid absent from the graph → no_data (decision #2: never fabricate).
    posted_dcids: list[str] = []
    for slot in inp.slots:
        binding = slot.binding
        if isinstance(binding, BindingValue) and binding.value.ref:
            posted_dcids.append(binding.value.ref.dcid)
        elif isinstance(binding, BindingSet):
            posted_dcids.extend(v.ref.dcid for v in binding.values if v.ref is not None)

    entity_dcid: str | None = None
    if inp.entity_dcids:
        entity_dcid = inp.entity_dcids[0]
        posted_dcids.append(entity_dcid)
    elif inp.slots:
        # Fall back to where-slot entity (scalar sentinel: first confirmed ref in set)
        for slot in inp.slots:
            if slot.key.axis == "where":
                if isinstance(slot.binding, BindingValue):
                    if slot.binding.value.ref:
                        entity_dcid = slot.binding.value.ref.dcid
                elif isinstance(slot.binding, BindingSet):
                    first = next((v for v in slot.binding.values if v.ref is not None), None)
                    if first is not None and first.ref is not None:
                        entity_dcid = first.ref.dcid
                break

    if posted_dcids:
        label_map = graph.node_labels_batch(posted_dcids)
        absent = [d for d in posted_dcids if d not in label_map]
        if absent:
            return _no_data_region(variable_text)

    # Convert posted Slot objects to SlotBindingDraft for the pipeline.
    bindings = _slots_to_binding_drafts(inp.slots)

    # Derive date_request from the when-slot when present.
    date_request = _date_request_from_slots(inp.slots)

    # Derive recipient_dcid: entity_dcids[0] takes precedence over posted slots.
    recipient_dcid = entity_dcid

    # Materialise via the named-family resolver (calls construct_sv_dcid → confirms SV).
    mat_result = materialise(
        shape_draft, bindings, recipient_dcid, donor_dcid=None,
        graph=graph, date_request=date_request,
    )
    if isinstance(mat_result, NoDataDraft):
        return RegionResult(
            variable_text=variable_text,
            status="no_data",
            specs=(),
            no_data_reason=mat_result.reason,
            warnings=(),
            timing_by_step={},
        )
    if not isinstance(mat_result, Materialised):
        # MaterialisedCandidates: named-family resubmit always yields one SV; treat as no_data.
        return _no_data_region(variable_text)

    # Build entity roles dict for _ground_answer.
    roles: dict[str, EntityRoleDraft] = {}
    if recipient_dcid:
        roles[recipient_dcid] = EntityRoleDraft(
            dcid=recipient_dcid,
            surface=None,
            role=DirectionalRole(kind="directional", direction="to", role_dcid=RECIPIENT_ROLE_DCID),
        )
    # When the where-slot is a BindingSet, add all recipients as directional "to" roles
    # so the returned Spec's entity list covers every posted recipient.
    for slot in inp.slots:
        if slot.key.axis == "where" and isinstance(slot.binding, BindingSet):
            for v in slot.binding.values:
                if v.ref is not None and v.ref.dcid not in roles:
                    roles[v.ref.dcid] = EntityRoleDraft(
                        dcid=v.ref.dcid,
                        surface=None,
                        role=DirectionalRole(
                            kind="directional", direction="to", role_dcid=RECIPIENT_ROLE_DCID
                        ),
                    )
            break

    grounding = _ground_answer(
        shape_draft, bindings, mat_result.sv_dcids, roles, graph, mat_result.facets
    )
    (
        five_tuple_refs, role_refs, slots, slot_key_models, sv_refs, entity_data, source_refs
    ) = grounding

    shape_model = build_shape_model(
        shape_draft, slot_key_models, five_tuple_refs, member_count=len(sv_refs)
    )
    stat_vars = build_stat_vars(
        sv_refs,
        shape_draft.shape_id,
        slots,
        facets_by_sv=mat_result.facets_by_sv,
        recipient_confirmed=mat_result.recipient_confirmed,
    )
    entities = [_build_entity(rd, er, etr, role_refs) for rd, er, etr in entity_data]
    slots = bind_when_slot(slots, window=mat_result.coverage.window)
    spec = build_spec(
        shape=shape_model,
        slots=slots,
        stat_vars=stat_vars,
        entities=entities,
        coverage=mat_result.coverage,
        pipeline_trace=pipeline_steps,
        resolved_sources=source_refs,
    )

    return RegionResult(
        variable_text=variable_text,
        status="definite",
        specs=(spec,),
        no_data_reason=None,
        warnings=(),
        timing_by_step={},
    )


def _resolve_standard_promote(
    inp: SpecResubmitInput,
    graph: EngineGraphClient,
    pipeline_steps: list[PipelineStep],
) -> RegionResult:
    """Standard path: promote-only from re-read posted SV arcs.

    Raises EngineInputError for:
      - Missing stat_var_dcids (no code)
      - SVs spanning multiple five-tuples (no code)
      - shape_id mismatch (no code)
      - Edited bindings not in SV constraints (code="promote_only")

    Absent posted dcids → no_data, not an error.
    """
    variable_text = inp.shape_id

    if not inp.stat_var_dcids:
        raise EngineInputError(
            "standard resubmit requires stat_var_dcids; "
            "omit for named families (e.g. dev_finance_crs_dac)"
        )

    # Re-read arcs for every posted SV; any absent → no_data.
    arcs_map = graph.node_arcs_batch(inp.stat_var_dcids)
    absent_svs = [sv for sv, arcs in arcs_map.items() if arcs is None]
    if absent_svs:
        return _no_data_region(variable_text)
    # Narrow: all values are non-None after the above guard.
    confirmed_arcs: dict[str, dict] = {sv: a for sv, a in arcs_map.items() if a is not None}

    # Extract five-tuple for every SV; require all to share one five-tuple.
    five_tuples = {sv: read_five_tuple(confirmed_arcs[sv]) for sv in inp.stat_var_dcids}
    unique_fts = set(five_tuples.values())
    if len(unique_fts) > 1:
        raise EngineInputError(
            "posted stat_var_dcids span multiple five-tuple shapes; "
            "a candidate is one shape"
        )

    ft = next(iter(unique_fts))

    # Regenerate the standard shape_id from the re-read five-tuple (mirrors
    # discover.py:412-417) and require it to match inp.shape_id.  This stops the
    # engine silently resolving a shape the client did not name, and catches
    # dev-finance SVs posted under a standard shape_id.
    regenerated_shape_id = (
        f"{ft.pop_type_dcid}_{ft.meas_prop_dcid}"
        f"_{ft.stat_type_dcid}"
        + (f"_{ft.meas_qual_dcid}" if ft.meas_qual_dcid else "")
        + (f"_per_{ft.meas_denom_dcid}" if ft.meas_denom_dcid else "")
    ).lower()

    if regenerated_shape_id != inp.shape_id:
        raise EngineInputError(
            "shape_id does not match the five-tuple derived from the posted stat_var_dcids"
        )

    # Build union constraint map from all posted SVs' arc facts.
    union_constraints: dict[str, set[str]] = {}
    for sv in inp.stat_var_dcids:
        sv_constraints = read_constraints(confirmed_arcs[sv])
        for prop, val in sv_constraints.items():
            union_constraints.setdefault(prop, set()).add(val)

    # Guard against refinement (editing slots beyond promote): only validate what/how slots
    # whose property is a key in the constraint map.  Skip where/when/source (geographic
    # entities are never enum constraints and must not false-400 an honest promote).
    for slot in inp.slots:
        if slot.key.axis not in ("what", "how"):
            continue
        prop_ref = slot.key.property
        prop = prop_ref.dcid if prop_ref is not None else None
        if prop is None or prop not in union_constraints:
            continue
        binding = slot.binding
        if isinstance(binding, BindingValue) and binding.value.ref:
            if binding.value.ref.dcid not in union_constraints[prop]:
                raise EngineInputError(
                    "standard resubmit is promote-only; edited bindings are not supported — "
                    "submit a raw_text query to change the interpretation",
                    code="promote_only",
                )
        elif isinstance(binding, BindingSet):
            for sv_val in binding.values:
                if sv_val.ref and sv_val.ref.dcid not in union_constraints[prop]:
                    raise EngineInputError(
                        "standard resubmit is promote-only; edited bindings are not supported — "
                        "submit a raw_text query to change the interpretation",
                        code="promote_only",
                    )

    # entity_dcids[0] takes precedence over a where-slot entity.
    entity_dcid: str | None = None
    if inp.entity_dcids:
        entity_dcid = inp.entity_dcids[0]
    else:
        # Fall back to the where-slot entity.
        for slot in inp.slots:
            if slot.key.axis == "where" and isinstance(slot.binding, BindingValue):
                if slot.binding.value.ref:
                    entity_dcid = slot.binding.value.ref.dcid
                break

    # Honest re-read: confirm entity dcids via node_labels_batch (decision #2).
    # Constraint values are already confirmed by being in the SV's arc facts;
    # _ground_answer will silently drop any that lack a standalone label.
    # Absent entity → no_data (no entity to probe against).
    if entity_dcid:
        label_map = graph.node_labels_batch([entity_dcid])
        if entity_dcid not in label_map:
            return _no_data_region(variable_text)

    # Reconstruct ShapeDraft from the re-read five-tuple and per-SV arc facts.
    # Build constraint_props, prop_labels, prop_observed_values from the arcs.
    # Use the first SV as the representative (insertion-order stable).
    rep_sv = inp.stat_var_dcids[0]
    rep_arcs = confirmed_arcs[rep_sv]
    rep_constraints = read_constraints(rep_arcs)
    constraint_props = list(rep_constraints.keys())

    # Collect all observed values per property across all SVs for classify_axis.
    prop_observed_values: dict[str, list[str]] = {p: [] for p in constraint_props}
    for sv in inp.stat_var_dcids:
        sv_c = read_constraints(confirmed_arcs[sv])
        for prop, val in sv_c.items():
            if prop in prop_observed_values:
                prop_observed_values[prop].append(val)

    # Derive prop labels from node_labels_batch (decision #2: graph-read only).
    prop_label_map = graph.node_labels_batch(constraint_props) if constraint_props else {}
    prop_labels = {p: prop_label_map.get(p, p) for p in constraint_props}

    sv_arc_facts = {sv: confirmed_arcs[sv] for sv in inp.stat_var_dcids}

    shape_draft = shape_draft_from(
        shape_id=regenerated_shape_id,
        label=regenerated_shape_id,  # standard label; grounding will set the anchors
        pop_type_dcid=ft.pop_type_dcid,
        meas_prop_dcid=ft.meas_prop_dcid,
        stat_type_dcid=ft.stat_type_dcid,
        meas_qual_dcid=ft.meas_qual_dcid,
        meas_denom_dcid=ft.meas_denom_dcid,
        constraint_props=constraint_props,
        prop_labels=prop_labels,
        prop_observed_values=prop_observed_values,
        family_rule=STANDARD_RULE,
        sv_arc_facts=sv_arc_facts,
    )

    # Build bindings: where + arc-derived constraints.
    bindings: list[SlotBindingDraft] = []
    if entity_dcid:
        bindings.append(SlotBindingDraft(
            axis="where", property_dcid=None, kind="value", value_dcids=[entity_dcid],
        ))
    bindings.extend(standard_bindings_from_arcs(shape=shape_draft, rep_sv_dcid=rep_sv))

    date_request = _date_request_from_slots(inp.slots)

    # Materialise via the standard resolver (probes the representative SV).
    mat_result = materialise(
        shape_draft, bindings, entity_dcid, donor_dcid=None,
        graph=graph, date_request=date_request,
    )
    if isinstance(mat_result, NoDataDraft):
        return RegionResult(
            variable_text=variable_text,
            status="no_data",
            specs=(),
            no_data_reason=mat_result.reason,
            warnings=(),
            timing_by_step={},
        )
    if not isinstance(mat_result, Materialised):
        # MaterialisedCandidates: standard promote re-reads one fixed SV; treat as no_data.
        return _no_data_region(variable_text)

    # Build entity roles dict for _ground_answer (standard = subject role).
    roles: dict[str, EntityRoleDraft] = {}
    if entity_dcid:
        roles[entity_dcid] = EntityRoleDraft(
            dcid=entity_dcid,
            surface=None,
            role=SubjectRole(),
        )

    grounding = _ground_answer(
        shape_draft, bindings, mat_result.sv_dcids, roles, graph, mat_result.facets
    )
    (
        five_tuple_refs, role_refs, slots, slot_key_models, sv_refs, entity_data, source_refs
    ) = grounding

    shape_model = build_shape_model(
        shape_draft, slot_key_models, five_tuple_refs, member_count=len(sv_refs)
    )
    stat_vars = build_stat_vars(
        sv_refs,
        shape_draft.shape_id,
        slots,
        facets_by_sv=mat_result.facets_by_sv,
        recipient_confirmed=mat_result.recipient_confirmed,
    )
    entities = [_build_entity(rd, er, etr, role_refs) for rd, er, etr in entity_data]
    slots = bind_when_slot(slots, window=mat_result.coverage.window)
    spec = build_spec(
        shape=shape_model,
        slots=slots,
        stat_vars=stat_vars,
        entities=entities,
        coverage=mat_result.coverage,
        pipeline_trace=pipeline_steps,
        resolved_sources=source_refs,
    )

    return RegionResult(
        variable_text=variable_text,
        status="definite",
        specs=(spec,),
        no_data_reason=None,
        warnings=(),
        timing_by_step={},
    )
