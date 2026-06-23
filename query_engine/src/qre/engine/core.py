"""QRE engine core: async pipeline orchestration.

Main entry points:
  resolve_async(request, *, graph=None, llm=None) → ResolveResponse (async)
  resolve(request) → ResolveResponse (sync wrapper)

Pipeline: extract → recall → shape → bind → materialise → ground → assemble.
Phase 0 processes the first variable only.
"""
from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from qre.engine.assemble import (
    assemble_definite,
    assemble_no_data,
    build_shape_model,
    build_slot,
    build_spec,
    build_stat_vars,
)
from qre.engine.bind import SlotBindingDraft, bind
from qre.engine.config import ENGINE_BUILD_ID, QRE_SEAM_DEFAULT
from qre.engine.errors import GroundingMiss
from qre.engine.extract import Extraction, extract
from qre.engine.families import (
    PURPOSES,
    RECIPIENT_ROLE_DCID,
    SCHEMES,
)
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
from qre.engine.retrieve import NoDataDraft, materialise
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

    # Process the first variable (Phase 0 single-variable pipeline)
    variable = extraction.variables[0]
    entities = extraction.entities

    # --- Step: recall ---
    t0 = _now_ms()
    rcl: Recall = await recall(variable, entities, graph=graph, raw_query=query)
    timing["recall"] = _now_ms() - t0
    pipeline_steps.append(_make_pipeline_step("recall", ran=True, ms=timing["recall"]))

    # --- Step: shape ---
    t0 = _now_ms()
    family = family_for(rcl.candidate_svs)
    if family is None:
        timing["shape"] = _now_ms() - t0
        pipeline_steps.append(_make_pipeline_step("shape", ran=True, ms=timing["shape"]))
        for step in ("bind", "materialise", "answer"):
            pipeline_steps.append(_make_pipeline_step(step, ran=False))
        return _no_data("variable_not_resolved")

    shape_draft: ShapeDraft = build_shape(family)
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

    # Always run with pac=True to find the directional "to" entity for SV construction;
    # the seam flag only affects how roles are assigned in the response.
    roles_for_sv, _, directional_detected_sv = directional_roles(
        query,
        resolved_pairs,
        place_as_constraint=True,
        recipient_role_dcid=RECIPIENT_ROLE_DCID,
    )

    # When pac=True the two calls are identical; skip the redundant second call.
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
    slot_taxonomy: dict[str, list[str]] = {
        "what:DevelopmentFinanceScheme": list(SCHEMES),
        "how:DevelopmentFinancePurpose": list(PURPOSES),
        "where:DevelopmentFinanceRecipient": (
            [recipient_dcid] if recipient_dcid else []
        ),
    }

    # --- Step: bind ---
    t0 = _now_ms()
    bindings: list[SlotBindingDraft] = await bind(variable, slot_taxonomy, llm=llm)
    timing["bind"] = _now_ms() - t0
    pipeline_steps.append(_make_pipeline_step("bind", ran=True, ms=timing["bind"]))

    # The recipient is resolved deterministically (entity resolution + directional
    # detection), so its binding does not depend on the LLM. The bind prompt omits the
    # raw query for safety, so the LLM sees no place in the variable phrase and returns
    # the recipient unbound; override it with the known recipient dcid.
    # ponytail: the where slot is still offered to the LLM to keep the bind fixtures
    # stable. Drop it from the taxonomy and re-record to stop asking entirely.
    if recipient_dcid:
        for b in bindings:
            if b.axis == "where":
                b.kind = "value"
                b.value_dcids = [recipient_dcid]
                break

    # Denominator check for per-capita queries
    if "per capita" in variable.lower() and shape_draft.meas_denom_dcid is None:
        pipeline_steps.append(_make_pipeline_step("materialise", ran=False))
        pipeline_steps.append(_make_pipeline_step("answer", ran=False))
        return _no_data("denominator_not_available")

    # --- Step: materialise ---
    t0 = _now_ms()
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

    # Definite when there's exactly one spec
    return assemble_definite(spec, echo, diag)


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
