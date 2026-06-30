"""Unit tests for conjoin.py pure helper functions.

No graph or LLM calls — operates only on hand-built RegionResult/Spec objects.
Covers: five_tuple_key, collapse_same_shape (single-slot merge, set ordering/dedupe,
spec_id recompute, multislot residual), select_primary, cross_shape_present,
build_conjunction_warnings (all four warning codes), assemble_region and
combine_regions echo kwarg passthrough.
"""
from __future__ import annotations

from qre.engine.assemble import build_spec, now_ms
from qre.engine.conjoin import (
    CONJUNCTION_CROSS_SHAPE,
    CONJUNCTION_PART_AMBIGUOUS,
    CONJUNCTION_PART_NO_DATA,
    assemble_region,
    build_conjunction_warnings,
    collapse_same_shape,
    combine_regions,
    cross_shape_present,
    five_tuple_key,
    select_primary,
)
from qre.engine.regions import RegionResult
from qre.models import (
    BindingSet,
    BindingValue,
    CoverageBare,
    CoverageExact,
    GraphRef,
    PipelineStep,
    QueryEcho,
    Shape,
    Slot,
    SlotKey,
    SlotValue,
    StatusLiteral,
    StatVar,
)
from tests.engine._harness import ref_dcid

# ---------------------------------------------------------------------------
# Minimal builders
# ---------------------------------------------------------------------------


def _gr(dcid: str) -> GraphRef:
    return GraphRef(dcid=dcid, label=dcid)


def _shape(pop: str, mp: str, mq: str | None = None) -> Shape:
    return Shape(
        shape_id=f"{pop}/{mp}/{mq or ''}",
        label="Test",
        population_type=_gr(pop),
        measured_property=_gr(mp),
        stat_type=_gr("measuredValue"),
        measurement_qualifier=_gr(mq) if mq else None,
        measurement_denominator=None,
        slot_keys=[
            SlotKey(axis="what", property=_gr("constraintProp"), label="constraintProp")
        ],
        member_count=1,
    )


def _value_slot(prop: str, val: str) -> Slot:
    return Slot(
        key=SlotKey(axis="what", property=_gr(prop), label=prop),
        binding=BindingValue(
            value=SlotValue(ref=_gr(val), value_kind="enum_value")
        ),
    )


def _spec(
    shape: Shape, constraint_val: str, sv_dcid: str = "test_sv", variable_text: str | None = None
):
    slot = _value_slot("constraintProp", constraint_val)
    return build_spec(
        shape=shape,
        slots=[slot],
        stat_vars=[StatVar(ref=_gr(sv_dcid), shape_id=shape.shape_id, slot_values=[])],
        entities=[],
        coverage=CoverageBare(has_data=True),
        pipeline_trace=[PipelineStep(step="extract", ran=True)],
        variable_text=variable_text,
    )


def _region(
    spec,
    variable_text: str,
    *,
    status: StatusLiteral = "definite",
    earliest_index: int = 0,
    no_data_reason: str | None = None,
    extra_specs: tuple = (),
) -> RegionResult:
    if status == "definite":
        specs: tuple = (spec,)
    elif status == "candidates":
        specs = (spec,) + extra_specs
    else:
        specs = ()
    return RegionResult(
        variable_text=variable_text,
        status=status,
        specs=specs,
        no_data_reason=no_data_reason,
        warnings=(),
        timing_by_step={},
        earliest_index=earliest_index,
    )


# Common shapes for reuse across tests
_PERSON_SHAPE = _shape("Person", "count")
_ECON_SHAPE = _shape("EconomicActivity", "amount", mq="Nominal")


# ---------------------------------------------------------------------------
# five_tuple_key
# ---------------------------------------------------------------------------


def test_five_tuple_key_basic():
    spec = _spec(_PERSON_SHAPE, "any")
    key = five_tuple_key(spec)
    assert key == ("Person", "count", "measuredValue", None, None)


def test_five_tuple_key_with_qualifier():
    spec = _spec(_ECON_SHAPE, "any")
    key = five_tuple_key(spec)
    assert key == ("EconomicActivity", "amount", "measuredValue", "Nominal", None)


def test_five_tuple_key_distinct_shapes():
    spec_person = _spec(_PERSON_SHAPE, "any")
    spec_econ = _spec(_ECON_SHAPE, "any")
    assert five_tuple_key(spec_person) != five_tuple_key(spec_econ)


# ---------------------------------------------------------------------------
# collapse_same_shape — single-slot merge
# ---------------------------------------------------------------------------


def test_collapse_same_shape_single_slot_merges():
    """Two definite regions with same five-tuple and one differing value slot → merged."""
    spec_a = _spec(_PERSON_SHAPE, "ValA", variable_text="var A")
    spec_b = _spec(_PERSON_SHAPE, "ValB", variable_text="var B")
    r_a = _region(spec_a, "var A", earliest_index=0)
    r_b = _region(spec_b, "var B", earliest_index=1)

    effective, residual = collapse_same_shape([r_a, r_b])

    assert len(effective) == 1
    assert residual == []
    merged = effective[0]
    assert merged.status == "definite"
    merged_binding = merged.spec.slots[0].binding
    assert isinstance(merged_binding, BindingSet)
    merged_dcids = {ref_dcid(v.ref) for v in merged_binding.values}
    assert merged_dcids == {"ValA", "ValB"}


def test_collapse_same_shape_set_ordering_preserves_first():
    """Merged BindingSet keeps insertion order: first region's value appears first."""
    spec_a = _spec(_PERSON_SHAPE, "Alpha", variable_text="var A")
    spec_b = _spec(_PERSON_SHAPE, "Beta", variable_text="var B")
    r_a = _region(spec_a, "var A", earliest_index=0)
    r_b = _region(spec_b, "var B", earliest_index=1)

    effective, _ = collapse_same_shape([r_a, r_b])

    binding = effective[0].spec.slots[0].binding
    assert isinstance(binding, BindingSet)
    assert ref_dcid(binding.values[0].ref) == "Alpha"
    assert ref_dcid(binding.values[1].ref) == "Beta"


def test_collapse_same_shape_dedupe_same_value():
    """Identical slot value across regions: < 2 distinct refs, so the multislot rule applies."""
    spec_a = _spec(_PERSON_SHAPE, "SameVal", variable_text="var A")
    spec_b = _spec(_PERSON_SHAPE, "SameVal", variable_text="var B")
    r_a = _region(spec_a, "var A", earliest_index=0)
    r_b = _region(spec_b, "var B", earliest_index=1)

    # < 2 distinct refs → SAME_SHAPE_MULTISLOT rule kicks in
    effective, residual = collapse_same_shape([r_a, r_b])

    assert len(effective) == 1
    assert residual == ["var B"]


def test_collapse_same_shape_spec_id_recomputed():
    """Merged spec has a new spec_id distinct from either input spec."""
    spec_a = _spec(_PERSON_SHAPE, "ValA", variable_text="var A")
    spec_b = _spec(_PERSON_SHAPE, "ValB", variable_text="var B")
    r_a = _region(spec_a, "var A", earliest_index=0)
    r_b = _region(spec_b, "var B", earliest_index=1)

    effective, _ = collapse_same_shape([r_a, r_b])

    merged_id = effective[0].spec.spec_id
    assert merged_id != spec_a.spec_id
    assert merged_id != spec_b.spec_id


def test_collapse_same_shape_earliest_index_is_min():
    """Merged region's earliest_index is the minimum of the group."""
    spec_a = _spec(_PERSON_SHAPE, "ValA", variable_text="var A")
    spec_b = _spec(_PERSON_SHAPE, "ValB", variable_text="var B")
    # r_b has earliest_index=0, r_a has earliest_index=2
    r_a = _region(spec_a, "var A", earliest_index=2)
    r_b = _region(spec_b, "var B", earliest_index=0)

    effective, _ = collapse_same_shape([r_a, r_b])

    assert effective[0].earliest_index == 0


def test_collapse_same_shape_passthrough_single_region():
    """A single definite region passes through unchanged (no merge)."""
    spec_a = _spec(_PERSON_SHAPE, "ValA", variable_text="var A")
    r_a = _region(spec_a, "var A")

    effective, residual = collapse_same_shape([r_a])

    assert len(effective) == 1
    assert effective[0] is r_a
    assert residual == []


# ---------------------------------------------------------------------------
# collapse_same_shape — multislot residual rule
# ---------------------------------------------------------------------------


def test_same_shape_multislot_residual_warns():
    """Same five-tuple, >1 slot differs: keep first; residual_texts gets the second's text."""
    # Build two shapes with DIFFERENT constraint props so both slots differ
    shape_two_slots = Shape(
        shape_id="Person/count/",
        label="Test",
        population_type=_gr("Person"),
        measured_property=_gr("count"),
        stat_type=_gr("measuredValue"),
        measurement_qualifier=None,
        measurement_denominator=None,
        slot_keys=[
            SlotKey(axis="what", property=_gr("propA"), label="propA"),
            SlotKey(axis="how", property=_gr("propB"), label="propB"),
        ],
        member_count=1,
    )

    def _two_slot_spec(val_a: str, val_b: str, variable_text: str):
        return build_spec(
            shape=shape_two_slots,
            slots=[
                Slot(
                    key=SlotKey(axis="what", property=_gr("propA"), label="propA"),
                    binding=BindingValue(value=SlotValue(ref=_gr(val_a), value_kind="enum_value")),
                ),
                Slot(
                    key=SlotKey(axis="how", property=_gr("propB"), label="propB"),
                    binding=BindingValue(value=SlotValue(ref=_gr(val_b), value_kind="enum_value")),
                ),
            ],
            stat_vars=[],
            entities=[],
            coverage=CoverageBare(has_data=True),
            pipeline_trace=[PipelineStep(step="extract", ran=True)],
            variable_text=variable_text,
        )

    spec_1 = _two_slot_spec("A1", "B1", "var 1")
    spec_2 = _two_slot_spec("A2", "B2", "var 2")
    r1 = _region(spec_1, "var 1", earliest_index=0)
    r2 = _region(spec_2, "var 2", earliest_index=1)

    effective, residual = collapse_same_shape([r1, r2])

    # SAME_SHAPE_MULTISLOT: first stays effective, second is residual
    assert len(effective) == 1
    assert effective[0] is r1
    assert residual == ["var 2"]


def test_same_shape_multislot_no_cross_shape_warning():
    """Residual from SAME_SHAPE_MULTISLOT emits PART_AMBIGUOUS, NOT CONJUNCTION_CROSS_SHAPE.

    After collapse_same_shape, only one effective region remains → cross_shape_present=False
    → build_conjunction_warnings emits no CONJUNCTION_CROSS_SHAPE.
    """
    shape_two_slots = Shape(
        shape_id="Person/count/",
        label="Test",
        population_type=_gr("Person"),
        measured_property=_gr("count"),
        stat_type=_gr("measuredValue"),
        measurement_qualifier=None,
        measurement_denominator=None,
        slot_keys=[
            SlotKey(axis="what", property=_gr("propA"), label="propA"),
            SlotKey(axis="how", property=_gr("propB"), label="propB"),
        ],
        member_count=1,
    )
    spec_1 = build_spec(
        shape=shape_two_slots,
        slots=[
            Slot(
                key=SlotKey(axis="what", property=_gr("propA"), label="propA"),
                binding=BindingValue(value=SlotValue(ref=_gr("A1"), value_kind="enum_value")),
            ),
            Slot(
                key=SlotKey(axis="how", property=_gr("propB"), label="propB"),
                binding=BindingValue(value=SlotValue(ref=_gr("B1"), value_kind="enum_value")),
            ),
        ],
        stat_vars=[],
        entities=[],
        coverage=CoverageBare(has_data=True),
        pipeline_trace=[PipelineStep(step="extract", ran=True)],
        variable_text="var 1",
    )
    spec_2 = build_spec(
        shape=shape_two_slots,
        slots=[
            Slot(
                key=SlotKey(axis="what", property=_gr("propA"), label="propA"),
                binding=BindingValue(value=SlotValue(ref=_gr("A2"), value_kind="enum_value")),
            ),
            Slot(
                key=SlotKey(axis="how", property=_gr("propB"), label="propB"),
                binding=BindingValue(value=SlotValue(ref=_gr("B2"), value_kind="enum_value")),
            ),
        ],
        stat_vars=[],
        entities=[],
        coverage=CoverageBare(has_data=True),
        pipeline_trace=[PipelineStep(step="extract", ran=True)],
        variable_text="var 2",
    )
    r1 = _region(spec_1, "var 1", earliest_index=0)
    r2 = _region(spec_2, "var 2", earliest_index=1)

    effective, residual_texts = collapse_same_shape([r1, r2])

    # Construct the additional_interpretations as None (same-shape residual ≠ cross-shape)
    primary = effective[0]
    extras: list[RegionResult] = []  # no extras from collapse — residual went to residual_texts

    # build_conjunction_warnings for the effective-only case
    warnings = build_conjunction_warnings(primary, extras, ["var 1", "var 2"])
    codes = [w.code for w in warnings]

    assert CONJUNCTION_CROSS_SHAPE not in codes
    # The residual PART_AMBIGUOUS warning is built in combine_regions from residual_texts,
    # not in build_conjunction_warnings; so none here either
    assert CONJUNCTION_PART_AMBIGUOUS not in codes


# ---------------------------------------------------------------------------
# select_primary
# ---------------------------------------------------------------------------


def test_select_primary_first_definite_by_earliest_index():
    """Primary is the first definite by earliest_index."""
    spec = _spec(_PERSON_SHAPE, "any")
    r0_nodata = _region(spec, "nd", status="no_data", earliest_index=0)
    r1_def = _region(spec, "def", status="definite", earliest_index=1)
    r2_def = _region(spec, "def2", status="definite", earliest_index=2)

    primary, extras = select_primary([r0_nodata, r1_def, r2_def])

    assert primary is r1_def
    assert r0_nodata in extras
    assert r2_def in extras


def test_select_primary_fallback_when_no_definite():
    """When no definite regions, primary = effective[0]."""
    spec = _spec(_PERSON_SHAPE, "any")
    r0 = _region(spec, "nd0", status="no_data", earliest_index=0)
    r1 = _region(spec, "nd1", status="no_data", earliest_index=1)

    primary, extras = select_primary([r0, r1])

    assert primary is r0
    assert extras == [r1]


def test_select_primary_extras_sorted_by_earliest_index():
    """Extras are sorted by earliest_index."""
    spec = _spec(_PERSON_SHAPE, "any")
    r0 = _region(spec, "r0", earliest_index=0)
    r2 = _region(spec, "r2", earliest_index=2)
    r4 = _region(spec, "r4", earliest_index=4)

    primary, extras = select_primary([r4, r2, r0])  # intentionally shuffled

    assert primary is r0
    assert [e.earliest_index for e in extras] == [2, 4]


# ---------------------------------------------------------------------------
# cross_shape_present
# ---------------------------------------------------------------------------


def test_cross_shape_present_false_no_extras():
    spec = _spec(_PERSON_SHAPE, "any")
    r = _region(spec, "primary")

    assert not cross_shape_present(r, [])


def test_cross_shape_present_true_non_definite_extra():
    """A non-definite extra always makes cross_shape_present True."""
    spec = _spec(_PERSON_SHAPE, "any")
    primary = _region(spec, "primary")
    extra = _region(spec, "extra", status="candidates")

    assert cross_shape_present(primary, [extra])


def test_cross_shape_present_true_different_five_tuple():
    """Definite extras with a different five-tuple → True."""
    spec_person = _spec(_PERSON_SHAPE, "any")
    spec_econ = _spec(_ECON_SHAPE, "any")
    primary = _region(spec_person, "population")
    extra = _region(spec_econ, "GDP")

    assert cross_shape_present(primary, [extra])


def test_cross_shape_present_false_same_five_tuple_extras():
    """Definite extras sharing the primary's five-tuple → False.

    After Tier-1 same-shape collapse this case never occurs in production,
    but the predicate is defensively correct.
    """
    spec_a = _spec(_PERSON_SHAPE, "ValA")
    spec_b = _spec(_PERSON_SHAPE, "ValB")
    primary = _region(spec_a, "var A")
    extra = _region(spec_b, "var B")

    assert not cross_shape_present(primary, [extra])


# ---------------------------------------------------------------------------
# build_conjunction_warnings
# ---------------------------------------------------------------------------


def test_build_conjunction_warnings_cross_shape():
    """CONJUNCTION_CROSS_SHAPE emitted when cross-shape detected."""
    spec_person = _spec(_PERSON_SHAPE, "any")
    spec_econ = _spec(_ECON_SHAPE, "any")
    primary = _region(spec_person, "population")
    extra_gdp = _region(spec_econ, "GDP")

    warnings = build_conjunction_warnings(primary, [extra_gdp], ["population", "GDP"])

    codes = [w.code for w in warnings]
    assert CONJUNCTION_CROSS_SHAPE in codes
    cross_w = next(w for w in warnings if w.code == CONJUNCTION_CROSS_SHAPE)
    assert cross_w.severity == "info"
    assert "population" in cross_w.message
    assert "GDP" in cross_w.message


def test_build_conjunction_warnings_part_ambiguous():
    """CONJUNCTION_PART_AMBIGUOUS emitted for a candidates extra."""
    spec = _spec(_PERSON_SHAPE, "any")
    primary = _region(spec, "primary")
    extra = _region(spec, "gdp", status="candidates", earliest_index=1)

    warnings = build_conjunction_warnings(primary, [extra], ["primary", "gdp"])

    codes = [w.code for w in warnings]
    assert CONJUNCTION_PART_AMBIGUOUS in codes
    w = next(w for w in warnings if w.code == CONJUNCTION_PART_AMBIGUOUS)
    assert w.severity == "warn"
    assert "gdp" in w.message


def test_build_conjunction_warnings_part_no_data():
    """CONJUNCTION_PART_NO_DATA emitted for a no_data extra."""
    spec = _spec(_PERSON_SHAPE, "any")
    primary = _region(spec, "primary")
    extra = _region(spec, "malaria", status="no_data", earliest_index=1)

    warnings = build_conjunction_warnings(primary, [extra], ["primary", "malaria"])

    codes = [w.code for w in warnings]
    assert CONJUNCTION_PART_NO_DATA in codes
    w = next(w for w in warnings if w.code == CONJUNCTION_PART_NO_DATA)
    assert w.severity == "warn"
    assert "malaria" in w.message


def test_build_conjunction_warnings_definite_extra_no_warning():
    """Definite extras are silent (they ride additional_interpretations, not warnings)."""
    spec_person = _spec(_PERSON_SHAPE, "any")
    spec_econ = _spec(_ECON_SHAPE, "any")
    primary = _region(spec_person, "population")
    extra = _region(spec_econ, "GDP")  # status=definite

    warnings = build_conjunction_warnings(primary, [extra], ["population", "GDP"])

    # CONJUNCTION_CROSS_SHAPE is present (different shapes), but no PART_* warnings
    codes = [w.code for w in warnings]
    assert CONJUNCTION_CROSS_SHAPE in codes
    assert CONJUNCTION_PART_AMBIGUOUS not in codes
    assert CONJUNCTION_PART_NO_DATA not in codes


# ---------------------------------------------------------------------------
# collapse_same_shape — CoverageExact downgrade
# ---------------------------------------------------------------------------


def test_collapse_same_shape_exact_coverage_downgraded():
    """Merged spec from CoverageExact members must not carry the first member's exact count.

    collapse_same_shape unions stat_vars from all members but is pure (no graph),
    so it cannot recount observations across the merged set. Carrying CoverageExact
    would falsely claim member-0's observation_count applies to all unioned stat_vars.
    """
    spec_a = build_spec(
        shape=_PERSON_SHAPE,
        slots=[_value_slot("constraintProp", "ValA")],
        stat_vars=[StatVar(ref=_gr("sv_a"), shape_id=_PERSON_SHAPE.shape_id, slot_values=[])],
        entities=[],
        coverage=CoverageExact(has_data=True, observation_count=50, dimensions=None, window=None),
        pipeline_trace=[PipelineStep(step="extract", ran=True)],
        variable_text="var A",
    )
    spec_b = build_spec(
        shape=_PERSON_SHAPE,
        slots=[_value_slot("constraintProp", "ValB")],
        stat_vars=[StatVar(ref=_gr("sv_b"), shape_id=_PERSON_SHAPE.shape_id, slot_values=[])],
        entities=[],
        coverage=CoverageExact(has_data=True, observation_count=30, dimensions=None, window=None),
        pipeline_trace=[PipelineStep(step="extract", ran=True)],
        variable_text="var B",
    )
    r_a = _region(spec_a, "var A", earliest_index=0)
    r_b = _region(spec_b, "var B", earliest_index=1)

    effective, residual = collapse_same_shape([r_a, r_b])

    assert len(effective) == 1
    assert residual == []
    assert effective[0].spec.coverage.kind != "exact", (
        "merged spec must not claim member-0's exact observation_count for the unioned stat_vars"
    )


def test_build_conjunction_warnings_pinned_messages():
    """Message templates are consistent for all conjunction warning codes."""
    spec_person = _spec(_PERSON_SHAPE, "any")
    spec_econ = _spec(_ECON_SHAPE, "any")
    primary = _region(spec_person, "population")
    extra_gdp = _region(spec_econ, "GDP")
    extra_malaria = _region(spec_econ, "malaria deaths", status="no_data", earliest_index=2)

    warnings = build_conjunction_warnings(
        primary, [extra_gdp, extra_malaria], ["population", "GDP", "malaria deaths"]
    )

    by_code = {w.code: w for w in warnings}

    assert by_code[CONJUNCTION_CROSS_SHAPE].message == (
        "Distinct measures conjoined: population; GDP; malaria deaths."
    )
    assert by_code[CONJUNCTION_PART_NO_DATA].message == (
        "Variable 'malaria deaths' returned no data."
    )


# ---------------------------------------------------------------------------
# echo kwarg passthrough
# ---------------------------------------------------------------------------


def _assemble_kwargs(region: RegionResult, **overrides):
    """Minimal keyword args for assemble_region / combine_regions."""
    return dict(
        query="test query",
        variable_texts=[region.variable_text],
        extra_warnings=[],
        start_ms=now_ms(),
        engine_build="test",
        include_sentence=False,
        max_candidates=10,
    ) | overrides


def _spec_resubmit_echo(variable_text: str = "aid to ethiopia") -> QueryEcho:
    return QueryEcho(
        entry_path="spec_resubmit",
        raw_query=None,
        normalized_query=None,
        variable_text=[variable_text],
        extract_skipped=True,
    )


def test_assemble_region_default_echo_uses_raw_text():
    """Without an explicit echo, assemble_region builds entry_path='raw_text'."""
    spec = _spec(_PERSON_SHAPE, "any")
    region = _region(spec, "population")
    response = assemble_region(region, **_assemble_kwargs(region))
    assert response.root.query_echo.entry_path == "raw_text"
    assert response.root.query_echo.extract_skipped is False


def test_assemble_region_supplied_echo_used_verbatim():
    """When echo is supplied, assemble_region uses it verbatim (entry_path preserved)."""
    spec = _spec(_PERSON_SHAPE, "any")
    region = _region(spec, "aid to ethiopia")
    supplied = _spec_resubmit_echo("aid to ethiopia")
    response = assemble_region(region, echo=supplied, **_assemble_kwargs(region))
    assert response.root.query_echo.entry_path == "spec_resubmit"
    assert response.root.query_echo.extract_skipped is True
    assert response.root.query_echo.raw_query is None


def test_combine_regions_default_echo_uses_raw_text():
    """Without an explicit echo, combine_regions builds entry_path='raw_text'."""
    spec_a = _spec(_PERSON_SHAPE, "any", sv_dcid="sv_a")
    spec_b = _spec(_ECON_SHAPE, "any", sv_dcid="sv_b")
    regions = [
        _region(spec_a, "population", earliest_index=0),
        _region(spec_b, "GDP", earliest_index=1),
    ]
    response = combine_regions(
        regions,
        query="population and GDP",
        variable_texts=["population", "GDP"],
        extra_warnings=[],
        start_ms=now_ms(),
        engine_build="test",
        include_sentence=False,
        max_candidates=10,
    )
    assert response.root.query_echo.entry_path == "raw_text"


def test_combine_regions_supplied_echo_used_verbatim():
    """When echo is supplied, combine_regions uses it verbatim across all branches."""
    spec_a = _spec(_PERSON_SHAPE, "any", sv_dcid="sv_a")
    spec_b = _spec(_ECON_SHAPE, "any", sv_dcid="sv_b")
    regions = [
        _region(spec_a, "population", earliest_index=0),
        _region(spec_b, "GDP", earliest_index=1),
    ]
    supplied = _spec_resubmit_echo("population and GDP")
    response = combine_regions(
        regions,
        query="population and GDP",
        variable_texts=["population", "GDP"],
        extra_warnings=[],
        start_ms=now_ms(),
        engine_build="test",
        include_sentence=False,
        max_candidates=10,
        echo=supplied,
    )
    assert response.root.query_echo.entry_path == "spec_resubmit"
    assert response.root.query_echo.extract_skipped is True


def test_combine_regions_full_collapse_safety_net_threads_echo():
    """The full-collapse safety net (len(effective)==1) threads echo into assemble_region."""
    # Two regions with the same five-tuple collapse to one — triggers the safety net.
    spec_a = _spec(_PERSON_SHAPE, "grants", sv_dcid="sv_grants")
    spec_b = _spec(_PERSON_SHAPE, "loans", sv_dcid="sv_loans")
    regions = [
        _region(spec_a, "grants", earliest_index=0),
        _region(spec_b, "loans", earliest_index=1),
    ]
    supplied = _spec_resubmit_echo("grants and loans")
    response = combine_regions(
        regions,
        query="grants and loans",
        variable_texts=["grants", "loans"],
        extra_warnings=[],
        start_ms=now_ms(),
        engine_build="test",
        include_sentence=False,
        max_candidates=10,
        echo=supplied,
    )
    # After same-shape collapse the safety net calls assemble_region with the echo.
    assert response.root.query_echo.entry_path == "spec_resubmit"
    assert response.root.query_echo.extract_skipped is True


# ---------------------------------------------------------------------------
# collapse_same_shape propagates value_kind from source binding
# ---------------------------------------------------------------------------


def test_collapse_same_shape_propagates_enum_value_kind():
    """Merged BindingSet carries value_kind from the source slot, not hardcoded 'entity'.

    _value_slot builds what-axis slots with value_kind='enum_value'. After collapse,
    the merged BindingSet values must also carry 'enum_value', not 'entity'.
    """
    spec_a = _spec(_PERSON_SHAPE, "ValA", variable_text="var A")
    spec_b = _spec(_PERSON_SHAPE, "ValB", variable_text="var B")
    r_a = _region(spec_a, "var A", earliest_index=0)
    r_b = _region(spec_b, "var B", earliest_index=1)

    effective, residual = collapse_same_shape([r_a, r_b])

    assert len(effective) == 1
    assert residual == []
    merged_binding = effective[0].spec.slots[0].binding
    assert isinstance(merged_binding, BindingSet)
    # value_kind must come from the source slot (enum_value), not hardcoded entity
    for sv in merged_binding.values:
        assert sv.value_kind == "enum_value", (
            f"expected 'enum_value' from source binding, got {sv.value_kind!r}"
        )
