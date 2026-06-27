"""Assemble stage: build grounded Spec and ResolveResponse from pipeline parts.

Pure assembly: no LLM, no graph calls.
"""
from __future__ import annotations

from qre.engine.bind import SlotBindingDraft
from qre.engine.shape import ShapeDraft, SlotKeyDraft
from qre.engine.spec_id import compute_spec_id
from qre.models import (
    BindingAbsent,
    BindingSet,
    BindingUnbound,
    BindingValue,
    CandidateSet,
    CandidatesResponse,
    Coverage,
    DefiniteResponse,
    Diagnostics,
    Entity,
    GraphRef,
    NoData,
    NoDataResponse,
    PipelineStep,
    QueryEcho,
    ResolutionTrace,
    ResolvedFilter,
    ResolveResponse,
    Shape,
    Slot,
    SlotKey,
    SlotValue,
    StatVar,
    StatVarSlotValue,
)
from qre.render import no_data_phrase, render_candidates_summary, render_sentence


def build_slot(
    slot_key_draft: SlotKeyDraft,
    binding_draft: SlotBindingDraft | None,
    grounded_values: list[GraphRef],
    *,
    property_ref: GraphRef | None = None,
) -> Slot:
    """Build a grounded Slot from draft components.

    Args:
        slot_key_draft: The ungrounded slot identity (axis, property_dcid, label).
        binding_draft: The LLM's binding for this slot. None means absent/when/source.
        grounded_values: Confirmed GraphRef values from the graph (for value/set bindings).
        property_ref: The grounded GraphRef for the slot's property. None for when/source slots.

    Returns:
        A grounded Slot with the appropriate binding arm.
    """
    slot_key = SlotKey(
        axis=slot_key_draft.axis,
        property=property_ref,
        label=slot_key_draft.label,
    )

    # When/source axis slots — always unbound in the default case
    if slot_key_draft.axis in ("when", "source"):
        return Slot(key=slot_key, binding=BindingUnbound())

    if binding_draft is None:
        return Slot(key=slot_key, binding=BindingUnbound())

    kind = binding_draft.kind

    if kind == "value":
        if grounded_values:
            sv = SlotValue(ref=grounded_values[0], value_kind="entity")
        else:
            # Grounding failed — fall back to unbound
            return Slot(key=slot_key, binding=BindingUnbound())
        return Slot(key=slot_key, binding=BindingValue(value=sv))

    if kind == "set":
        if len(grounded_values) < 2:  # noqa: PLR2004
            # Not enough confirmed values for a set — fall back to unbound
            return Slot(key=slot_key, binding=BindingUnbound())
        slot_values = [SlotValue(ref=gv, value_kind="entity") for gv in grounded_values]
        return Slot(key=slot_key, binding=BindingSet(values=slot_values))

    if kind == "unbound":
        return Slot(key=slot_key, binding=BindingUnbound())

    if kind == "absent":
        return Slot(key=slot_key, binding=BindingAbsent())

    # Unknown kind — default to unbound
    return Slot(key=slot_key, binding=BindingUnbound())


def build_shape_model(
    shape_draft: ShapeDraft,
    slot_keys: list[SlotKey],
    five_tuple_refs: dict[str, GraphRef],
    member_count: int,
) -> Shape:
    """Build a grounded Shape from a ShapeDraft and confirmed GraphRefs.

    Args:
        shape_draft: The ungrounded shape with five-tuple dcids.
        slot_keys: Grounded SlotKey objects (one per slot).
        five_tuple_refs: Maps five-tuple dcid → GraphRef for each component.
        member_count: Number of confirmed StatVar members.

    Returns:
        A grounded Shape model.
    """
    pop_ref = five_tuple_refs.get(shape_draft.pop_type_dcid)
    meas_ref = (
        five_tuple_refs.get(shape_draft.meas_prop_dcid) if shape_draft.meas_prop_dcid else None
    )
    stat_ref = five_tuple_refs.get(shape_draft.stat_type_dcid)
    qual_ref = (
        five_tuple_refs.get(shape_draft.meas_qual_dcid) if shape_draft.meas_qual_dcid else None
    )
    denom_ref = (
        five_tuple_refs.get(shape_draft.meas_denom_dcid)
        if shape_draft.meas_denom_dcid
        else None
    )

    # pop_type, meas_prop, and stat_type are required; if absent from the graph, use a
    # placeholder so the Spec can be built even when the fixture/graph lacks the node.
    if pop_ref is None:
        pop_ref = GraphRef(dcid=shape_draft.pop_type_dcid, label=shape_draft.pop_type_dcid)
    if meas_ref is None and shape_draft.meas_prop_dcid:
        meas_ref = GraphRef(dcid=shape_draft.meas_prop_dcid, label=shape_draft.meas_prop_dcid)
    if stat_ref is None:
        stat_ref = GraphRef(dcid=shape_draft.stat_type_dcid, label=shape_draft.stat_type_dcid)

    return Shape(
        shape_id=shape_draft.shape_id,
        label=shape_draft.label,
        population_type=pop_ref,
        measured_property=meas_ref,
        stat_type=stat_ref,
        measurement_qualifier=qual_ref,
        measurement_denominator=denom_ref,
        slot_keys=slot_keys,
        member_count=member_count,
    )


def build_stat_vars(
    sv_refs: list[GraphRef],
    shape_id: str,
    slots: list[Slot],
) -> list[StatVar]:
    """Build StatVar objects from confirmed GraphRefs and their slot values.

    Each StatVar carries the slot values from the current binding. For value
    and set bindings the slot's value(s) become StatVarSlotValue entries.

    Args:
        sv_refs: Confirmed SV GraphRefs.
        shape_id: The shape_id back-reference.
        slots: Grounded slots with binding information.

    Returns:
        List of StatVar objects.
    """
    # Collect slot values for the binding
    sv_slot_values: list[StatVarSlotValue] = []
    for slot in slots:
        if slot.key.axis in ("when", "source"):
            continue
        binding = slot.binding
        if isinstance(binding, BindingValue):
            sv_slot_values.append(StatVarSlotValue(key=slot.key, value=binding.value))
        elif isinstance(binding, BindingSet):
            # Add set binding once (representative across all SVs).
            for sv in binding.values:
                sv_slot_values.append(StatVarSlotValue(key=slot.key, value=sv))
            break

    return [
        StatVar(ref=sv_ref, shape_id=shape_id, slot_values=sv_slot_values)
        for sv_ref in sv_refs
    ]


def build_spec(
    shape: Shape,
    slots: list[Slot],
    stat_vars: list[StatVar],
    entities: list[Entity],
    coverage: Coverage,
    pipeline_trace: list[PipelineStep],
    timing_by_step: dict[str, int],
) -> "Spec":  # noqa: F821 — imported below
    """Assemble a Spec with a deterministic spec_id.

    Args:
        shape: The grounded Shape model.
        slots: Grounded slots with binding state.
        stat_vars: Confirmed StatVar objects.
        entities: Resolved Entity objects.
        coverage: Observation footprint.
        pipeline_trace: Pipeline step records.
        timing_by_step: Step name → latency in ms.

    Returns:
        A fully-assembled Spec.
    """
    from qre.models import BindingKind, Spec  # late imports to avoid circular

    spec_id = compute_spec_id(shape.shape_id, slots)

    resolved_sv_refs = [sv.ref for sv in stat_vars]
    resolved_entity_refs = [e.ref for e in entities]

    # Build slot_filters for resolution trace
    slot_filters: list[ResolvedFilter] = []
    for slot in slots:
        binding = slot.binding
        refs: list[GraphRef] = []
        if isinstance(binding, BindingValue) and binding.value.ref:
            refs = [binding.value.ref]
        elif isinstance(binding, BindingSet):
            refs = [sv.ref for sv in binding.values if sv.ref]

        kind: BindingKind = binding.kind  # type: ignore[assignment]

        slot_filters.append(
            ResolvedFilter(key=slot.key, binding_kind=kind, refs=refs)
        )

    resolution = ResolutionTrace(
        resolved_stat_vars=resolved_sv_refs,
        resolved_entities=resolved_entity_refs,
        resolved_sources=[],
        slot_filters=slot_filters,
        applied_window=coverage.window,
        date_source="query" if coverage.window is not None else None,
        pipeline_trace=pipeline_trace,
    )

    return Spec(
        spec_id=spec_id,
        shape=shape,
        slots=slots,
        stat_vars=stat_vars,
        entities=entities,
        coverage=coverage,
        resolution=resolution,
    )


def assemble_definite(
    spec: "Spec",  # noqa: F821
    query_echo: QueryEcho,
    diagnostics: Diagnostics,
    *,
    include_sentence: bool = False,
) -> ResolveResponse:
    """Wrap a Spec into a DefiniteResponse."""
    rendered = render_sentence(spec) if include_sentence else None
    return ResolveResponse(
        root=DefiniteResponse(
            query_echo=query_echo,
            diagnostics=diagnostics,
            interpretation=spec,
            rendered_sentence=rendered,
        )
    )


def assemble_no_data(
    reason: str,
    query_echo: QueryEcho,
    diagnostics: Diagnostics,
    *,
    include_sentence: bool = False,
) -> ResolveResponse:
    """Build a NoDataResponse with the given reason."""
    rendered = no_data_phrase(reason) if include_sentence else None
    return ResolveResponse(
        root=NoDataResponse(
            query_echo=query_echo,
            diagnostics=diagnostics,
            no_data=NoData(reason=reason),  # type: ignore[arg-type]
            rendered_sentence=rendered,
        )
    )


def assemble_candidates(
    specs: list["Spec"],  # noqa: F821
    query_echo: QueryEcho,
    diagnostics: Diagnostics,
    *,
    max_candidates: int | None = None,
    include_sentence: bool = False,
) -> ResolveResponse:
    """Build a CandidatesResponse from multiple competing Specs.

    Specs are sorted broadest-first (descending member_count, then spec_id
    lexicographic tiebreak) and clamped to max_candidates.

    Args:
        specs:            Competing Spec objects (must have len >= 2 before clamping).
        query_echo:       The query echo to include in the response.
        diagnostics:      The diagnostics envelope.
        max_candidates:   Upper bound on the number of specs. None defaults to len(specs).
        include_sentence: When True, render a count summary in rendered_sentence.

    Returns:
        A ResolveResponse wrapping a CandidatesResponse.
    """
    cap = max_candidates if max_candidates is not None else len(specs)

    # Sort broadest-first: highest member_count, then spec_id.
    sorted_specs = sorted(specs, key=lambda s: (-s.shape.member_count, s.spec_id))
    clamped = sorted_specs[:cap]

    # Count reflects the specs actually returned, not the pre-clamp total.
    rendered = render_candidates_summary(len(clamped)) if include_sentence else None

    return ResolveResponse(
        root=CandidatesResponse(
            query_echo=query_echo,
            diagnostics=diagnostics,
            candidates=CandidateSet(
                max_candidates=cap,
                specs=clamped,
            ),
            rendered_sentence=rendered,
        )
    )
