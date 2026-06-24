"""QRE engine core: async pipeline orchestration.

Main entry points:
  resolve_async(request, *, graph=None, llm=None) → ResolveResponse (async)
  resolve(request) → ResolveResponse (sync wrapper)

Pipeline: extract → recall → shape → bind → materialise → ground → assemble.
Processes the first variable only (single-variable pipeline).
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from qre.engine.assemble import (
    assemble_candidates,
    assemble_definite,
    assemble_no_data,
    build_shape_model,
    build_slot,
    build_spec,
    build_stat_vars,
)
from qre.engine.bind import SlotBindingDraft, bind
from qre.engine.config import (
    ENGINE_BUILD_ID,
    QRE_DOMINANCE_MARGIN,
    QRE_MAX_CANDIDATES,
    QRE_SEAM_DEFAULT,
)
from qre.engine.discover import (
    derive_shapes,
    filter_offtopic_shapes,
    read_constraints,
    read_slot_taxonomy,
)
from qre.engine.errors import GroundingMiss
from qre.engine.extract import Extraction, extract
from qre.engine.families import (
    PROP_RECIPIENT,
    RECIPIENT_ROLE_DCID,
)
from qre.engine.families.dev_finance import DEV_FINANCE_RULE
from qre.engine.families.registry import REGISTRY, STANDARD_RULE
from qre.engine.graph import EngineGraphClient, LiveGraphClient
from qre.engine.ground import graphref, graphrefs
from qre.engine.interpret import Recall, recall
from qre.engine.llm import LLM
from qre.engine.place_role import (
    SEAM_OFF_INFO_CODE,
    SEAM_OFF_WARN_CODE,
    DirectionalRole,
    EntityRoleDraft,
    SubjectRole,
    directional_roles,
)
from qre.engine.retrieve import Materialised, MaterialisedCandidates, NoDataDraft, materialise
from qre.engine.shape import ShapeDraft, build_shape, family_for
from qre.models import (
    Diagnostics,
    Entity,
    EntityRoleDirectional,
    EntityRoleSubject,
    GraphRef,
    PipelineStep,
    QueryEcho,
    ResolveRequest,
    ResolveResponse,
    Timing,
    Warning,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_ms() -> int:
    return int(time.monotonic() * 1000)


def _make_query_echo(
    query: str,
    variable_text: list[str],
    extract_skipped: bool,
) -> QueryEcho:
    return QueryEcho(
        entry_path="raw_text",
        raw_query=query,
        normalized_query=query.strip() if query.strip() else None,
        variable_text=variable_text,
        extract_skipped=extract_skipped,
    )


def _make_diagnostics(
    engine_build: str,
    warnings: list[Warning],
    timing_by_step: dict[str, int],
    total_ms: int,
) -> Diagnostics:
    return Diagnostics(
        engine_build=engine_build,
        warnings=warnings,
        timing_ms=Timing(total=total_ms, by_step=timing_by_step or None),
    )


def _make_pipeline_step(
    step: str,
    ran: bool,
    ms: int | None = None,
) -> PipelineStep:
    return PipelineStep(step=step, ran=ran, ms=ms)  # type: ignore[arg-type]


def _build_entity(
    role_draft: EntityRoleDraft,
    entity_ref: GraphRef,
    entity_type_ref: GraphRef | None,
    recipient_role_ref: GraphRef | None,
) -> Entity:
    """Build a grounded Entity from a role draft."""
    role = role_draft.role
    if isinstance(role, DirectionalRole) and role.kind == "directional":
        entity_role = EntityRoleDirectional(
            kind="directional",
            role=recipient_role_ref or GraphRef(dcid=role.role_dcid, label=role.role_dcid),
            direction=role.direction,
        )
    else:
        entity_role = EntityRoleSubject()

    return Entity(ref=entity_ref, entity_type=entity_type_ref, role=entity_role)


# ---------------------------------------------------------------------------
# Standard-family disambiguation
# ---------------------------------------------------------------------------


def _top_dominates(std_shapes: list[ShapeDraft], *, margin: float) -> bool:
    """Return True when the top standard shape's representative-SV cosine score exceeds
    the second's by at least ``margin``.

    The dominance rule ranks shapes in core.py (not in resolvers) so that shape ranking
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


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


async def _resolve_pipeline(
    request: ResolveRequest,
    *,
    graph: EngineGraphClient,
    llm: LLM,
    start_ms: int,
) -> ResolveResponse:
    """Core pipeline body. Graph and LLM are always provided; lifecycle is the caller's concern."""
    warnings: list[Warning] = []
    timing: dict[str, int] = {}
    pipeline_steps: list[PipelineStep] = []

    inp = request.input

    def _no_data(
        reason: str,
        variables: list[str] | None = None,
        extract_skipped: bool = False,
    ) -> ResolveResponse:
        total_ms = _now_ms() - start_ms
        echo = _make_query_echo(
            query,
            variables if variables is not None else [variable],
            extract_skipped=extract_skipped,
        )
        diag = _make_diagnostics(ENGINE_BUILD_ID, warnings, timing, total_ms)
        return assemble_no_data(reason, echo, diag)

    if inp.kind != "raw_text":
        # spec_resubmit → not yet implemented; parsed → app layer rejects with 400
        return _quick_no_data(
            reason="variable_not_resolved",
            query="",
            engine_build=ENGINE_BUILD_ID,
            start_ms=start_ms,
        )

    query: str = inp.query  # type: ignore[union-attr]

    # Empty/whitespace query check
    if not query.strip():
        pipeline_steps.append(_make_pipeline_step("extract", ran=False))
        return _no_data("variable_not_resolved", variables=[], extract_skipped=True)

    # Seam flag from request options or config default
    pac: bool
    if request.options and request.options.place_as_constraint is not None:
        pac = request.options.place_as_constraint
    else:
        pac = QRE_SEAM_DEFAULT

    # --- Step: extract ---
    t0 = _now_ms()
    extraction: Extraction = await extract(query, llm=llm)
    timing["extract"] = _now_ms() - t0
    pipeline_steps.append(_make_pipeline_step("extract", ran=True, ms=timing["extract"]))

    if not extraction.variables:
        return _no_data("variable_not_resolved", variables=[])

    # Process the first variable (single-variable pipeline)
    variable = extraction.variables[0]
    entities = extraction.entities

    # --- Step: recall ---
    t0 = _now_ms()
    rcl: Recall = await recall(variable, entities, graph=graph, raw_query=query)
    timing["recall"] = _now_ms() - t0
    pipeline_steps.append(_make_pipeline_step("recall", ran=True, ms=timing["recall"]))

    # --- Step: shape ---
    # Graph-derived shape discovery: confirm each candidate SV via node_arcs,
    # group by five-tuple, build a ShapeDraft per group.
    # The blocking graph reads run in a worker thread.
    t0 = _now_ms()
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
            timing["shape"] = _now_ms() - t0
            pipeline_steps.append(_make_pipeline_step("shape", ran=True, ms=timing["shape"]))
            for step in ("bind", "materialise", "answer"):
                pipeline_steps.append(_make_pipeline_step(step, ran=False))
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
        # This matches the original "else: std_shapes + df_shapes" fallback branch.
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
            pipeline_steps.append(_make_pipeline_step(step, ran=False))
        return _no_data("variable_not_resolved")

    # Candidate-noise filter: drop STANDARD shapes whose representative SV's display
    # label shares no content-word token with the variable phrase.  Applied only to
    # standard shapes; dev-finance shapes pass through untouched.
    shapes_found = filter_offtopic_shapes(shapes_found, variable=variable)
    if not shapes_found:
        for step in ("bind", "materialise", "answer"):
            pipeline_steps.append(_make_pipeline_step(step, ran=False))
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
    timing["shape"] = _now_ms() - t0
    pipeline_steps.append(_make_pipeline_step("shape", ran=True, ms=timing["shape"]))

    # Any named entity that failed to resolve fires entity_not_resolved to ensure
    # the engine never silently answers a different query.
    distinct_extracted = len(set(entities))
    if entities and len(rcl.resolved_entity_names) < distinct_extracted:
        for step in ("bind", "materialise", "answer"):
            pipeline_steps.append(_make_pipeline_step(step, ran=False))
        return _no_data("entity_not_resolved")

    # --- Place role classification ---
    resolved_pairs: list[tuple[str, str | None]] = [
        (dcid, surface)
        for surface, dcid in rcl.resolved_entity_names.items()
    ]

    # Find the directional recipient entity for SV construction (always with pac=True);
    # the seam flag affects only how roles are presented in the response.
    roles_for_sv, _, directional_detected_sv = directional_roles(
        query,
        resolved_pairs,
        place_as_constraint=True,
        recipient_role_dcid=RECIPIENT_ROLE_DCID,
    )

    # When seam=ON (pac=True) the two role calls would be identical; skip the redundant second.
    if pac:
        roles = roles_for_sv
        seam_off = False
        directional_detected = False
    else:
        roles, seam_off, directional_detected = directional_roles(
            query,
            resolved_pairs,
            place_as_constraint=False,
            recipient_role_dcid=RECIPIENT_ROLE_DCID,
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
    for dcid, role_draft in roles_for_sv.items():
        if isinstance(role_draft.role, DirectionalRole) and role_draft.role.direction == "to":
            recipient_dcid = dcid
        elif isinstance(role_draft.role, SubjectRole):
            donor_dcid = dcid

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

    # --- Build slot taxonomy for bind ---
    # Use the per-shape taxonomy stamped by discover.read_slot_taxonomy.
    # For dev-finance this is the hand-verified seed (B1); for standard it is the
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

    if _has_constraint_slots:
        # Inject the deterministically-resolved recipient into the where slot.
        # This mirrors the existing dev-finance behaviour: the LLM is shown the
        # recipient but the binding is overwritten below regardless of what it returns.
        where_key = f"where:{PROP_RECIPIENT}"
        slot_taxonomy[where_key] = [recipient_dcid] if recipient_dcid else []

        # --- Step: bind ---
        t0 = _now_ms()
        bindings: list[SlotBindingDraft] = await bind(variable, slot_taxonomy, llm=llm)
        timing["bind"] = _now_ms() - t0
        pipeline_steps.append(_make_pipeline_step("bind", ran=True, ms=timing["bind"]))

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
            where_binding.kind = "value"
            where_binding.value_dcids = [recipient_dcid]
    else:
        # No constraint slots (from slot_taxonomy): skip LLM bind entirely (standard family
        # with bare count SV, etc.).  Build a minimal where-only binding from the resolved
        # entity so the grounding stage has a recipient to display.  No property_dcid for
        # the entity-only where slot (matches the SlotKeyDraft(property_dcid=None) pattern).
        pipeline_steps.append(_make_pipeline_step("bind", ran=False))
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
        pipeline_steps.append(_make_pipeline_step("materialise", ran=False))
        pipeline_steps.append(_make_pipeline_step("answer", ran=False))
        return _no_data("denominator_not_available")

    # --- Step: materialise ---
    # For multiple standard shapes (candidates path): rank+clamp by member_count,
    # probe each shape's representative SV, and collect into MaterialisedCandidates.
    # For a single shape (definite path): delegate to the single-shape materialise path.
    t0 = _now_ms()
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
        )
    else:
        mat_result = await asyncio.to_thread(
            materialise,
            shape_draft,
            bindings,
            recipient_dcid,
            donor_dcid,
            graph=graph,
        )

    timing["materialise"] = _now_ms() - t0
    pipeline_steps.append(_make_pipeline_step("materialise", ran=True, ms=timing["materialise"]))

    if isinstance(mat_result, NoDataDraft):
        pipeline_steps.append(_make_pipeline_step("answer", ran=False))
        return _no_data(mat_result.reason)

    # --- Step: answer (ground everything and assemble Spec) ---
    t0 = _now_ms()

    if isinstance(mat_result, MaterialisedCandidates):
        # Candidates path: ground the representative SV for each surviving shape,
        # build a Spec per shape, then assemble into a CandidatesResponse.
        specs: list = []
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
            )
            (
                ft_refs,
                rr_ref,
                cand_slots,
                cand_slot_key_models,
                cand_sv_refs,
                cand_entity_data,
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
            cand_stat_vars = build_stat_vars(cand_sv_refs, cand_shape.shape_id, cand_slots)
            cand_entities = [
                _build_entity(rd, er, etr, rr_ref)
                for rd, er, etr in cand_entity_data
            ]
            cand_spec = build_spec(
                shape=cand_shape_model,
                slots=cand_slots,
                stat_vars=cand_stat_vars,
                entities=cand_entities,
                coverage=cand_mat.coverage,
                pipeline_trace=pipeline_steps,
                timing_by_step=timing,
            )
            specs.append(cand_spec)

        # Dedupe by spec_id (shapes with identical bindings collapse to one spec)
        seen_spec_ids: set[str] = set()
        unique_specs = []
        for s in specs:
            if s.spec_id not in seen_spec_ids:
                seen_spec_ids.add(s.spec_id)
                unique_specs.append(s)

        timing["answer"] = _now_ms() - t0
        pipeline_steps.append(_make_pipeline_step("answer", ran=True, ms=timing["answer"]))

        if len(unique_specs) == 0:
            return _no_data("no_observations")
        if len(unique_specs) == 1:
            total_ms = _now_ms() - start_ms
            echo = _make_query_echo(query, [variable], extract_skipped=False)
            diag = _make_diagnostics(ENGINE_BUILD_ID, warnings, timing, total_ms)
            return assemble_definite(unique_specs[0], echo, diag)

        total_ms = _now_ms() - start_ms
        echo = _make_query_echo(query, [variable], extract_skipped=False)
        diag = _make_diagnostics(ENGINE_BUILD_ID, warnings, timing, total_ms)
        return assemble_candidates(unique_specs, echo, diag, max_candidates=QRE_MAX_CANDIDATES)

    # Definite path: single Materialised result.
    grounding_result = await asyncio.to_thread(
        _ground_answer,
        shape_draft,
        bindings,
        mat_result.sv_dcids,
        roles,
        graph,
    )

    (
        five_tuple_refs,
        recipient_role_ref,
        slots,
        slot_key_models,
        sv_refs,
        entity_objects_data,
    ) = grounding_result

    # Build the Shape model
    shape_model = build_shape_model(
        shape_draft,
        slot_key_models,
        five_tuple_refs,
        member_count=len(sv_refs),
    )

    # Build StatVars
    stat_vars = build_stat_vars(sv_refs, shape_draft.shape_id, slots)

    # Assemble Entity objects from grounded data
    entity_objects: list[Entity] = [
        _build_entity(role_draft, entity_ref, entity_type_ref, recipient_role_ref)
        for role_draft, entity_ref, entity_type_ref in entity_objects_data
    ]

    timing["answer"] = _now_ms() - t0
    pipeline_steps.append(_make_pipeline_step("answer", ran=True, ms=timing["answer"]))

    # Assemble the Spec
    spec = build_spec(
        shape=shape_model,
        slots=slots,
        stat_vars=stat_vars,
        entities=entity_objects,
        coverage=mat_result.coverage,
        pipeline_trace=pipeline_steps,
        timing_by_step=timing,
    )

    total_ms = _now_ms() - start_ms
    echo = _make_query_echo(query, [variable], extract_skipped=False)
    diag = _make_diagnostics(ENGINE_BUILD_ID, warnings, timing, total_ms)

    return assemble_definite(spec, echo, diag)


def _materialise_standard_candidates(
    std_shapes: list[ShapeDraft],
    bindings: list[SlotBindingDraft],
    recipient_dcid: str | None,
    donor_dcid: str | None,
    graph: EngineGraphClient,
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
        result = materialise(shape, bindings, recipient_dcid, donor_dcid, graph=graph)
        if isinstance(result, Materialised):
            surviving.append((shape, result))

    if not surviving:
        return NoDataDraft(reason="no_observations"), []

    mat_list = [m for _, m in surviving]
    return MaterialisedCandidates(candidates=mat_list), surviving


def _ground_answer(
    shape_draft: ShapeDraft,
    bindings: list[SlotBindingDraft],
    sv_dcids: list[str],
    roles: dict,
    graph: EngineGraphClient,
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

    # Ground the recipient role node
    recipient_role_ref: GraphRef | None = None
    try:
        recipient_role_ref = graphref(RECIPIENT_ROLE_DCID, graph=graph)
    except GroundingMiss:
        recipient_role_ref = GraphRef(dcid=RECIPIENT_ROLE_DCID, label=RECIPIENT_ROLE_DCID)

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

        slot = build_slot(
            slot_draft,
            b_draft,
            grounded_vals,
            property_ref=prop_ref,
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

    return (
        five_tuple_refs,
        recipient_role_ref,
        slots,
        slot_key_models,
        sv_refs,
        entity_objects_data,
    )


async def resolve_async(
    request: ResolveRequest,
    *,
    graph: EngineGraphClient | None = None,
    llm: LLM | None = None,
) -> ResolveResponse:
    """Async pipeline: extract → recall → bind → materialise → assemble.

    Graph and LLM are injected; when None, live clients are built from env.

    Args:
        request: The typed ResolveRequest from the caller.
        graph: Graph client. When None, LiveGraphClient() is built (reads
            QRE_GRAPH_BASE from env).
        llm: LLM wrapper. When None, LLM() is built (reads GEMINI_API_KEY from env).

    Returns:
        A ResolveResponse (DefiniteResponse | CandidatesResponse | NoDataResponse).
    """
    start_ms = _now_ms()
    owns_graph = graph is None
    _graph = graph if graph is not None else LiveGraphClient()
    _llm = llm or LLM()

    try:
        return await _resolve_pipeline(
            request,
            graph=_graph,
            llm=_llm,
            start_ms=start_ms,
        )
    finally:
        if owns_graph and hasattr(_graph, "close"):
            _graph.close()


def _quick_no_data(
    *,
    reason: str,
    query: str,
    engine_build: str,
    start_ms: int,
) -> ResolveResponse:
    """Build a no_data response without any LLM or graph calls."""
    total_ms = _now_ms() - start_ms
    echo = QueryEcho(
        entry_path="raw_text",
        raw_query=query or None,
        normalized_query=None,
        variable_text=[],
        extract_skipped=True,
    )
    diag = _make_diagnostics(engine_build, [], {}, total_ms)
    return assemble_no_data(reason, echo, diag)


def resolve(request: ResolveRequest) -> ResolveResponse:
    """Sync wrapper around resolve_async.

    Builds LiveGraphClient and LLM from environment variables.
    For dependency injection (tests), use resolve_async(request, graph=..., llm=...).

    Loop-safe: callable both standalone and from within a running event loop
    (e.g. the Langfuse experiment runner, which awaits the task inside its loop).
    When a loop is already running, the pipeline runs in a worker thread with its
    own loop so asyncio.run does not nest.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(resolve_async(request))
    # ponytail: serial offload (one thread, blocks the caller); switch to an async
    # task seam if the eval runner ever needs items resolved concurrently.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(resolve_async(request))).result()
