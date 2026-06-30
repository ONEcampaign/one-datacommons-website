"""Assemble stage: build grounded Spec and ResolveResponse from pipeline parts.

Pure assembly: no LLM, no graph calls.

Public envelope helpers (now_ms, make_pipeline_step, make_query_echo, make_diagnostics)
live here so both core.py and conjoin.py can import them without cycles.
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

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
    DateRange,
    DateSource,
    DefiniteResponse,
    Diagnostics,
    Entity,
    EntryPath,
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
    TimeWindow,
    Timing,
    Warning,
)
from qre.render import no_data_phrase, render_candidates_summary, render_sentence

if TYPE_CHECKING:
    from qre.engine.graph import Facet
    from qre.models import BindingKind, Spec  # circular at runtime; safe under TYPE_CHECKING

# ---------------------------------------------------------------------------
# Envelope helpers — moved from core.py so conjoin.py can import without cycles
# ---------------------------------------------------------------------------


def now_ms() -> int:
    """Current monotonic time in milliseconds."""
    return int(time.monotonic() * 1000)


def make_pipeline_step(step: str, ran: bool, ms: int | None = None) -> PipelineStep:
    """Build a PipelineStep record."""
    return PipelineStep(step=step, ran=ran, ms=ms)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


def make_query_echo(
    query: str,
    variable_text: list[str],
    extract_skipped: bool,
    *,
    entry_path: EntryPath = "raw_text",
) -> QueryEcho:
    """Build a QueryEcho from the raw query text and variable list.

    entry_path defaults to "raw_text". Pass "spec_resubmit" (or "parsed") when
    assembling a response for a non-raw-text input so the echo faithfully reflects
    which entry path was taken.
    """
    return QueryEcho(
        entry_path=entry_path,
        raw_query=query,
        normalized_query=query.strip() or None,
        variable_text=variable_text,
        extract_skipped=extract_skipped,
    )


def make_diagnostics(
    engine_build: str,
    warnings: list[Warning],
    timing_by_step: dict[str, int],
    total_ms: int,
    *,
    llm_usage: dict[str, int] | None = None,
) -> Diagnostics:
    """Build a Diagnostics envelope.

    llm_usage is the aggregated token usage threaded up from the LLM calls
    (extract/bind return it; core sums it). None on the no-LLM early-return paths.
    """
    return Diagnostics(
        engine_build=engine_build,
        warnings=warnings,
        timing_ms=Timing(total=total_ms, by_step=timing_by_step or None),
        llm_usage=llm_usage,
    )


def build_slot(
    slot_key_draft: SlotKeyDraft,
    binding_draft: SlotBindingDraft | None,
    grounded_values: list[GraphRef],
    *,
    property_ref: GraphRef | None = None,
    set_ref: GraphRef | None = None,
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

    # Dispatch value_kind by axis: where-axis binds graph entities; what/how bind closed-enum
    # taxonomy values. When/source never reach here (early return above).
    vk = "entity" if slot_key_draft.axis == "where" else "enum_value"

    if kind == "value":
        if grounded_values:
            sv = SlotValue(ref=grounded_values[0], value_kind=vk)
        else:
            # Grounding failed — fall back to unbound
            return Slot(key=slot_key, binding=BindingUnbound())
        return Slot(key=slot_key, binding=BindingValue(value=sv))

    if kind == "set":
        # Axis-aware partial-grounding: on the where-axis, one confirmed value is better
        # than BindingUnbound; preserve it as BindingValue so spec_id stays stable and
        # the slot is not blanked when only one of two recipients grounds via graphrefs.
        if slot_key_draft.axis == "where" and len(grounded_values) == 1:
            sv = SlotValue(ref=grounded_values[0], value_kind="entity")
            return Slot(key=slot_key, binding=BindingValue(value=sv))
        if len(grounded_values) < 2:  # noqa: PLR2004
            # Not enough confirmed values for a set — fall back to unbound
            return Slot(key=slot_key, binding=BindingUnbound())
        slot_values = [SlotValue(ref=gv, value_kind=vk) for gv in grounded_values]
        return Slot(key=slot_key, binding=BindingSet(values=slot_values, set_ref=set_ref))

    if kind == "unbound":
        return Slot(key=slot_key, binding=BindingUnbound())

    if kind == "absent":
        return Slot(key=slot_key, binding=BindingAbsent())


def bind_when_slot(slots: list[Slot], *, window: TimeWindow | None) -> list[Slot]:
    """Bind the when-axis slot to a time_window value when a window was extracted.

    No-op when window is None. Source-slot stays BindingUnbound (no source-resolution
    path exists; binding it would fabricate). Order-preserving.
    """
    if window is None:
        return slots
    result = []
    for slot in slots:
        if slot.key.axis == "when":
            sv = SlotValue(ref=None, value_kind="time_window", time_window=window)
            slot = slot.model_copy(update={"binding": BindingValue(value=sv)})
        result.append(slot)
    return result


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

    # meas_prop is required (comment above); None only when meas_prop_dcid is absent,
    # which callers must not allow for a fully-grounded Shape.
    assert meas_ref is not None, "measured_property is required on a grounded Shape"

    # refine_supported: True for named families (non-empty stable shape_id);
    # standard shapes use the dynamic five-tuple shape_id and are promote-only.
    family_rule = shape_draft.family_rule
    refine_supported = bool(
        family_rule is not None and family_rule.shape_id
    )

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
        refine_supported=refine_supported,
    )


def build_stat_vars(
    sv_refs: list[GraphRef],
    shape_id: str,
    slots: list[Slot],
    *,
    facets_by_sv: "dict[str, list[Facet]] | None" = None,
    recipient_confirmed: "set[str] | None" = None,
) -> list[StatVar]:
    """Build StatVar objects from confirmed GraphRefs and their slot values.

    Each StatVar carries the slot values from the current binding. For value
    and set bindings the slot's value(s) become StatVarSlotValue entries.

    When facets_by_sv is provided, data_date_range is derived from the earliest
    and latest confirmed dates across the SV's facets. When recipient_confirmed
    is provided, data_confirmed_at_recipient is set on each StatVar.

    Args:
        sv_refs: Confirmed SV GraphRefs.
        shape_id: The shape_id back-reference.
        slots: Grounded slots with binding information.
        facets_by_sv: Optional per-SV facet lists, keyed by sv dcid.
        recipient_confirmed: Optional set of sv dcids confirmed directly by recipient.

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

    stat_vars: list[StatVar] = []
    for sv_ref in sv_refs:
        date_range: DateRange | None = None
        confirmed_at_recipient: bool | None = None

        if facets_by_sv is not None:
            sv_facets = facets_by_sv.get(sv_ref.dcid, [])
            start_dates = [f.earliest_date for f in sv_facets if f.earliest_date]
            end_dates = [f.latest_date for f in sv_facets if f.latest_date]
            if start_dates or end_dates:
                date_range = DateRange(
                    start=min(start_dates) if start_dates else None,
                    end=max(end_dates) if end_dates else None,
                )

        if recipient_confirmed is not None:
            confirmed_at_recipient = sv_ref.dcid in recipient_confirmed

        stat_vars.append(
            StatVar(
                ref=sv_ref,
                shape_id=shape_id,
                slot_values=sv_slot_values,
                data_date_range=date_range,
                data_confirmed_at_recipient=confirmed_at_recipient,
            )
        )

    return stat_vars


def build_spec(
    shape: Shape,
    slots: list[Slot],
    stat_vars: list[StatVar],
    entities: list[Entity],
    coverage: Coverage,
    pipeline_trace: list[PipelineStep],
    *,
    variable_text: str | None = None,
    resolved_sources: list[GraphRef] | None = None,
    n_recalled: int | None = None,
    date_source: DateSource | None = None,
) -> "Spec":  # noqa: F821 — imported below
    """Assemble a Spec with a deterministic spec_id.

    Args:
        shape: The grounded Shape model.
        slots: Grounded slots with binding state.
        stat_vars: Confirmed StatVar objects.
        entities: Resolved Entity objects.
        coverage: Observation footprint.
        pipeline_trace: Pipeline step records.
        variable_text: The variable text this Spec answers; None for single-variable responses.
        resolved_sources: Provenance GraphRefs resolved from observation facets; defaults to []
            when not supplied (e.g. hand-built specs in tests or conjunction paths).
        n_recalled: Count of SVs passing the relevance threshold before the confirm cap.
        date_source: Explicit DateSource override; when None, inferred as "query" when a
            coverage window is present, else None. Pass "coverage_clamp" or "default" when
            the window was not user-specified.

    Returns:
        A fully-assembled Spec.
    """
    from qre.models import (
        Spec,  # noqa: PLC0415 (late import to avoid circular; Spec used at runtime)
    )

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
        resolved_sources=resolved_sources or [],
        slot_filters=slot_filters,
        applied_window=coverage.window,
        date_source=date_source if date_source is not None else (
            "query" if coverage.window is not None else None
        ),
        pipeline_trace=pipeline_trace,
        n_recalled=n_recalled,
    )

    return Spec(
        spec_id=spec_id,
        shape=shape,
        slots=slots,
        stat_vars=stat_vars,
        entities=entities,
        coverage=coverage,
        resolution=resolution,
        variable_text=variable_text,
    )


def assemble_definite(
    spec: "Spec",  # noqa: F821
    query_echo: QueryEcho,
    diagnostics: Diagnostics,
    *,
    additional_interpretations: "list[Spec] | None" = None,  # noqa: F821 — imported below
    include_sentence: bool = False,
    n_measures: int = 1,
) -> ResolveResponse:
    """Wrap a Spec into a DefiniteResponse.

    Args:
        spec: The resolved Spec.
        query_echo: The query echo envelope.
        diagnostics: The diagnostics envelope.
        additional_interpretations: Other definite Specs for cross-shape conjunction;
            None for single-region responses, [] for interim (cross-shape detected,
            no resolved extras), populated list for resolved cross-shape parts.
        include_sentence: When True, render a confirmation sentence.
        n_measures: Total variables in the query; affects rendered_sentence formatting.
    """
    rendered = render_sentence(spec, n_measures=n_measures) if include_sentence else None
    return ResolveResponse(
        root=DefiniteResponse(
            query_echo=query_echo,
            diagnostics=diagnostics,
            interpretation=spec,
            additional_interpretations=additional_interpretations,
            rendered_sentence=rendered,
        )
    )


def assemble_no_data(
    reason: str,
    query_echo: QueryEcho,
    diagnostics: Diagnostics,
    *,
    include_sentence: bool = False,
    n_measures: int = 1,
    nearest_real: "list[Spec] | None" = None,
) -> ResolveResponse:
    """Build a NoDataResponse with the given reason.

    Args:
        reason: The NoDataReason code.
        query_echo: The query echo envelope.
        diagnostics: The diagnostics envelope.
        include_sentence: When True, render a no-data phrase.
        n_measures: Total variables in the query; affects rendered_sentence formatting.
        nearest_real: Optional grounded Specs adjacent to the request; confirmed reads only.
    """
    rendered = no_data_phrase(reason, n_measures=n_measures) if include_sentence else None  # ty: ignore[invalid-argument-type]
    return ResolveResponse(
        root=NoDataResponse(
            query_echo=query_echo,
            diagnostics=diagnostics,
            no_data=NoData(reason=reason, nearest_real=nearest_real),  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
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
    n_measures: int = 1,
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
        n_measures:       Total variables in the query; affects rendered_sentence formatting.

    Returns:
        A ResolveResponse wrapping a CandidatesResponse.
    """
    cap = max_candidates if max_candidates is not None else len(specs)

    # Sort broadest-first: highest member_count, then spec_id.
    sorted_specs = sorted(specs, key=lambda s: (-s.shape.member_count, s.spec_id))
    clamped = sorted_specs[:cap]

    # Count reflects the specs actually returned, not the pre-clamp total.
    rendered = (
        render_candidates_summary(len(clamped), n_measures=n_measures)
        if include_sentence
        else None
    )

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
