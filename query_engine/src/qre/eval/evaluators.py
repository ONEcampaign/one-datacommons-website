"""Six deterministic evaluators for the QRE eval harness.

Each item-level evaluator signature:
    fn(*, input, output, expected_output, metadata, **kwargs) -> Evaluation

Output is the task return value (a dict from model_dump). Evaluators parse it via
contract models when they need structure.

Graph-dependent evaluators are factories that close over a GraphClient:
    make_groundedness(graph) -> evaluator fn
    make_materialisation(graph) -> evaluator fn

Graph errors MUST propagate. An evaluator must never return 1.0 on a graph exception.

Checks that do not apply to a golden (e.g. interpretation_match on a candidates
golden) emit no Evaluation (return an empty list). Langfuse ingests every
Evaluation as a score and rejects null values, so a not-applicable check must
emit nothing rather than a null-valued score. Run-level aggregates skip the
absent entries the same way they skipped nulls.
"""
from __future__ import annotations

from typing import Iterator, get_args

from langfuse import Evaluation

from qre import (
    SCHEMA_VERSION,
    Axis,
    BindingSet,
    BindingValue,
    CoverageExact,
    DefiniteResponse,
    Entity,
    EntityRoleDirectional,
    GraphRef,
    ResolveResponse,
    Spec,
)
from qre.eval.graph import GraphClient

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_PLACE_PREFIXES = (
    "country/",
    "geoId/",
    "wikidataId/",
    "nuts/",
    "ipcc_50/",
    "isoCode/",
    "Earth",
    "africa",
    "asia",
    "europe",
    "oceania",
    "northamerica",
    "southamerica",
    "antarctica",
)


def _is_place_dcid(dcid: str) -> bool:
    """Return True if the dcid looks like a place identifier."""
    return any(dcid.startswith(p) or dcid == p for p in _PLACE_PREFIXES)


def _parse_response(output: dict) -> ResolveResponse | None:
    """Validate and return a ResolveResponse, or None on ValidationError.

    structural_conformance does its own validate-and-score; the other
    checks call this and return value=None on a None result (cannot score
    an unparseable response).
    """
    from pydantic import ValidationError

    try:
        return ResolveResponse.model_validate(output)
    except ValidationError:
        return None


def _iter_specs(resp: ResolveResponse) -> list[Spec]:
    """Return every Spec in the response, regardless of status."""
    root = resp.root
    if root.status == "definite":
        return [root.interpretation]
    if root.status == "candidates":
        return list(root.candidates.specs)
    if root.status == "no_data" and root.no_data.nearest_real:
        return list(root.no_data.nearest_real)
    return []


def _iter_graphrefs_in_spec(spec: Spec) -> Iterator[GraphRef]:
    """Yield every GraphRef position within a single Spec."""
    shape = spec.shape
    yield shape.population_type
    yield shape.measured_property
    yield shape.stat_type
    if shape.measurement_qualifier is not None:
        yield shape.measurement_qualifier
    if shape.measurement_denominator is not None:
        yield shape.measurement_denominator

    for slot in spec.slots:
        b = slot.binding
        if isinstance(b, BindingValue):
            if b.value.ref is not None:
                yield b.value.ref
        elif isinstance(b, BindingSet):
            for sv in b.values:
                if sv.ref is not None:
                    yield sv.ref

    for sv in spec.stat_vars:
        yield sv.ref

    for ent in spec.entities:
        yield ent.ref
        if ent.entity_type is not None:
            yield ent.entity_type
        if isinstance(ent.role, EntityRoleDirectional):
            yield ent.role.role


def _iter_graphrefs(resp: ResolveResponse) -> Iterator[GraphRef]:
    """Yield every GraphRef in the response, across all Spec positions."""
    for spec in _iter_specs(resp):
        yield from _iter_graphrefs_in_spec(spec)


# ---------------------------------------------------------------------------
# Check 1 -- structural_conformance
# ---------------------------------------------------------------------------


def structural_conformance(*, output, **kwargs) -> Evaluation:
    """Score 1.0 if the output validates against ResolveResponse and schema_version matches.

    Returns 0.0 with the first validation error in the comment otherwise.
    Applies to all goldens (never returns value=None).
    """
    from pydantic import ValidationError

    if not isinstance(output, dict):
        return Evaluation(
            name="structural_conformance",
            value=0.0,
            comment=f"output is not a dict: {type(output).__name__}",
        )

    schema_ver = output.get("schema_version")
    if schema_ver != SCHEMA_VERSION:
        return Evaluation(
            name="structural_conformance",
            value=0.0,
            comment=f"schema_version mismatch: got {schema_ver!r}, expected {SCHEMA_VERSION!r}",
        )

    try:
        ResolveResponse.model_validate(output)
        return Evaluation(name="structural_conformance", value=1.0)
    except ValidationError as exc:
        first_error = exc.errors()[0]
        return Evaluation(
            name="structural_conformance",
            value=0.0,
            comment=f"validation failed: {first_error['loc']} - {first_error['msg']}",
        )


# ---------------------------------------------------------------------------
# Check 2 -- groundedness (factory)
# ---------------------------------------------------------------------------


def make_groundedness(graph: GraphClient):
    """Return an evaluator that checks every GraphRef in the response against the graph.

    Counts fabricated refs (not found in graph) and walked refs. These counts are
    stored in Evaluation.metadata so the run-level aggregate can sum them correctly:
    rate = total fabricated / total walked (not a mean of per-item booleans).

    The evaluator MUST propagate graph errors. Never returns value=None.
    """

    def groundedness(*, output, **kwargs) -> Evaluation:
        resp = _parse_response(output)
        if resp is None:
            return Evaluation(
                name="groundedness",
                value=0.0,
                comment="response failed validation; cannot walk GraphRefs",
                metadata={"fabricated": 0, "walked": 0},
            )

        fabricated: list[str] = []
        walked = 0
        for ref in _iter_graphrefs(resp):
            walked += 1
            if not graph.exists(ref.dcid):
                fabricated.append(ref.dcid)

        value = 1.0 if not fabricated else 0.0
        comment = (
            f"walked={walked}, fabricated={len(fabricated)}: {fabricated}"
            if fabricated
            else f"walked={walked}, all grounded"
        )
        return Evaluation(
            name="groundedness",
            value=value,
            comment=comment,
            metadata={"fabricated": len(fabricated), "walked": walked},
        )

    return groundedness


# ---------------------------------------------------------------------------
# Check 3 -- interpretation_match
# ---------------------------------------------------------------------------


def _entity_tuple(ent: Entity) -> tuple:
    """Build a comparable 4-tuple from a model Entity, nulling out direction/role for subjects."""
    direction = ent.role.direction if isinstance(ent.role, EntityRoleDirectional) else None
    role_dcid = (
        ent.role.role.dcid if isinstance(ent.role, EntityRoleDirectional) else None
    )
    return (ent.ref.dcid, ent.role.kind, direction, role_dcid)


def _golden_entity_tuple(e: dict) -> tuple:
    """Build a comparable 4-tuple from a golden entity dict.

    Nulls out direction and role for subjects (direction and role_dcid become None).
    """
    gd = e["direction"] if e["role_kind"] == "directional" else None
    gr = e["role_dcid"] if e["role_kind"] == "directional" else None
    return (e["dcid"], e["role_kind"], gd, gr)


def _slot_value_key(slot):
    """Build a hashable value key from a model Slot binding."""
    b = slot.binding
    bk = b.kind
    if bk == "value":
        sv = b.value
        if sv.ref is not None:
            return sv.ref.dcid
        if sv.time_window is not None:
            return ("time_window", sv.time_window.start_year, sv.time_window.end_year)
        return sv.literal
    if bk == "set":
        return frozenset(v.ref.dcid for v in b.values if v.ref is not None)
    return None  # unbound or absent


def _golden_value_key(slot_dict: dict):
    """Build a hashable value key from a golden slot dict."""
    vd = slot_dict.get("value_dcid")
    bk = slot_dict["binding_kind"]
    if bk == "set" and isinstance(vd, list):
        return frozenset(vd)
    return vd  # str, None, or list handled as frozenset above


def interpretation_match(*, output, expected_output, **kwargs) -> Evaluation | list[Evaluation]:
    """Score 1.0 if the definite response matches all four golden dimensions.

    Emits no Evaluation for non-definite goldens (the aggregate skips absent items).

    Dimensions checked:
    1. shape five-tuple
    2. slots (axis, property_dcid, binding_kind, value_key) set comparison
    3. stat_vars sorted dcids
    4. entities 4-tuple set comparison
    """
    if expected_output.get("expected_status") != "definite":
        return []

    resp = _parse_response(output)
    if resp is None or resp.root.status != "definite":
        return Evaluation(
            name="interpretation_match",
            value=0.0,
            comment="response is not definite or failed validation",
        )

    interp = resp.root.interpretation

    # Dimension 1: shape five-tuple
    es = expected_output.get("expected_shape") or {}
    model_shape = (
        interp.shape.population_type.dcid,
        interp.shape.measured_property.dcid if interp.shape.measured_property else None,
        interp.shape.stat_type.dcid,
        interp.shape.measurement_qualifier.dcid if interp.shape.measurement_qualifier else None,
        interp.shape.measurement_denominator.dcid
        if interp.shape.measurement_denominator
        else None,
    )
    golden_shape = (
        es.get("population_type_dcid"),
        es.get("measured_property_dcid"),
        es.get("stat_type_dcid"),
        es.get("measurement_qualifier_dcid"),
        es.get("measurement_denominator_dcid"),
    )
    if model_shape != golden_shape:
        return Evaluation(
            name="interpretation_match",
            value=0.0,
            comment=f"shape mismatch: got {model_shape}, expected {golden_shape}",
        )

    # Dimension 2: slots
    # The corpus lists only the slots the expert verified. Extra model slots
    # (e.g. unbound when/source) are acceptable. Check that all golden slots
    # appear in the model and that no model slot contradicts a golden slot.
    model_slots = {
        (
            slot.key.axis,
            slot.key.property.dcid if slot.key.property else None,
            slot.binding.kind,
            _slot_value_key(slot),
        )
        for slot in interp.slots
    }
    golden_slots = {
        (
            s["axis"],
            s.get("property_dcid"),
            s["binding_kind"],
            _golden_value_key(s),
        )
        for s in (expected_output.get("expected_slots") or [])
    }
    missing = golden_slots - model_slots
    if missing:
        return Evaluation(
            name="interpretation_match",
            value=0.0,
            comment=f"slot mismatch: missing from model={missing}",
        )

    # Second pass: model slots whose (axis, property_dcid) pair is not covered by
    # any golden slot must be unbound or absent. A spurious value- or set-bound
    # slot means the model added information the expert did not pin.
    # Slots with no property (axis-only when/source slots) are exempt: the corpus
    # intentionally omits them, so they are always acceptable.
    golden_pairs = {
        (s["axis"], s.get("property_dcid"))
        for s in (expected_output.get("expected_slots") or [])
    }
    spurious = [
        slot
        for slot in interp.slots
        if slot.key.property is not None  # axis-only slots (when/source) are exempt
        and (slot.key.axis, slot.key.property.dcid) not in golden_pairs
        and slot.binding.kind not in {"unbound", "absent"}
    ]
    if spurious:
        spurious_desc = [
            (slot.key.axis, slot.key.property.dcid, slot.binding.kind)
            for slot in spurious
            if slot.key.property is not None
        ]
        return Evaluation(
            name="interpretation_match",
            value=0.0,
            comment=f"slot mismatch: spurious bound slots not in golden={spurious_desc}",
        )

    # Dimension 3: stat_vars
    model_svs = sorted(sv.ref.dcid for sv in interp.stat_vars)
    golden_svs = sorted(expected_output.get("expected_stat_vars") or [])
    if model_svs != golden_svs:
        return Evaluation(
            name="interpretation_match",
            value=0.0,
            comment=f"stat_var mismatch: got {model_svs}, expected {golden_svs}",
        )

    # Dimension 4: entities
    model_ents = {_entity_tuple(e) for e in interp.entities}
    golden_ents = {
        _golden_entity_tuple(e) for e in (expected_output.get("expected_entities") or [])
    }
    if model_ents != golden_ents:
        return Evaluation(
            name="interpretation_match",
            value=0.0,
            comment=(
                f"entity mismatch: extra={model_ents - golden_ents}, "
                f"missing={golden_ents - model_ents}"
            ),
        )

    return Evaluation(name="interpretation_match", value=1.0)


# ---------------------------------------------------------------------------
# Check 4 -- materialisation (factory)
# ---------------------------------------------------------------------------


def make_materialisation(graph: GraphClient):
    """Return an evaluator that checks coverage correctness for definite responses.

    Emits no Evaluation for non-definite goldens. Graph errors propagate.
    """

    def materialisation(*, output, expected_output, **kwargs) -> Evaluation | list[Evaluation]:
        if expected_output.get("expected_status") != "definite":
            return []

        resp = _parse_response(output)
        if resp is None or resp.root.status != "definite":
            return Evaluation(
                name="materialisation",
                value=0.0,
                comment="response is not definite or failed validation",
            )

        interp = resp.root.interpretation
        cov = interp.coverage

        if not cov.has_data:
            return Evaluation(
                name="materialisation",
                value=0.0,
                comment="coverage.has_data is False",
            )

        if cov.kind == "bare":
            return Evaluation(name="materialisation", value=1.0, comment="bare: has_data OK")

        if cov.kind == "breadth":
            bad = [d.label for d in cov.dimensions if d.count <= 0]
            if bad:
                return Evaluation(
                    name="materialisation",
                    value=0.0,
                    comment=f"breadth dims with count<=0: {bad}",
                )
            return Evaluation(
                name="materialisation",
                value=1.0,
                comment=f"breadth: has_data OK, dims={len(cov.dimensions)}",
            )

        if cov.kind == "exact":
            assert isinstance(cov, CoverageExact)
            stat_var_dcids = [sv.ref.dcid for sv in interp.stat_vars]
            entity_dcids = [e.ref.dcid for e in interp.entities]
            observed = graph.count_observations(
                stat_vars=stat_var_dcids,
                entities=entity_dcids,
                window=cov.window,
            )
            if observed is None:
                return Evaluation(
                    name="materialisation",
                    value=1.0,
                    comment="exact: graph returned no records; skipping +/-5% check (has_data OK)",
                )
            expected_count = cov.observation_count
            tolerance = max(1, expected_count * 0.05)
            if abs(observed - expected_count) > tolerance:
                return Evaluation(
                    name="materialisation",
                    value=0.0,
                    comment=(
                        f"exact: count mismatch: observed={observed}, "
                        f"expected={expected_count}, tolerance={tolerance:.1f}"
                    ),
                )
            return Evaluation(
                name="materialisation",
                value=1.0,
                comment=f"exact: observed={observed}, expected={expected_count} (within 5%)",
            )

        return Evaluation(
            name="materialisation",
            value=0.0,
            comment=f"unknown coverage kind: {cov.kind!r}",
        )

    return materialisation


# ---------------------------------------------------------------------------
# Check 5 -- behaviour_by_tag
# ---------------------------------------------------------------------------


def behaviour_by_tag(*, output, expected_output, metadata, **kwargs) -> list[Evaluation]:
    """Score behaviour match for the item's expected status, with a tag-scoped name.

    Emits exactly one of behaviour_match_definite, behaviour_match_candidates, or
    behaviour_match_no_data, depending on the item's behaviour tag. The other two
    buckets do not apply to this item and are not emitted. Run-level aggregates
    average each named evaluation independently over the items that carry it.
    """
    expected_status = expected_output.get("expected_status")
    resp = _parse_response(output)

    # Determine the behaviour tag from metadata.tags.
    tags = metadata.get("tags") or []
    behaviour_tag = next(
        (t["behaviour"] for t in tags if isinstance(t, dict) and "behaviour" in t),
        expected_status,  # fall back to expected_status when tag missing
    )

    def _score_definite() -> float:
        if resp is None or resp.root.status != "definite":
            return 0.0
        return 1.0

    def _score_candidates() -> float:
        if resp is None or resp.root.status != "candidates":
            return 0.0
        cs = resp.root.candidates
        specs = cs.specs
        if not (2 <= len(specs) <= cs.max_candidates):
            return 0.0
        spec_ids = [s.spec_id for s in specs]
        if len(spec_ids) != len(set(spec_ids)):
            return 0.0
        # Broadest-first ordering: ranked by member_count descending, spec_id as tiebreak
        expected_ids = [
            s.spec_id
            for s in sorted(specs, key=lambda s: (-s.shape.member_count, s.spec_id))
        ]
        if spec_ids != expected_ids:
            return 0.0
        return 1.0

    def _score_no_data() -> float:
        if resp is None or resp.root.status != "no_data":
            return 0.0
        expected_reason = expected_output.get("expected_no_data_reason")
        if resp.root.no_data.reason != expected_reason:
            return 0.0
        return 1.0

    # Emit only the bucket the item's behaviour tag selects; the others are not
    # applicable to this item and are left unscored.
    scorers = {
        "definite": ("behaviour_match_definite", _score_definite),
        "candidates": ("behaviour_match_candidates", _score_candidates),
        "no_data": ("behaviour_match_no_data", _score_no_data),
    }
    selected = scorers.get(behaviour_tag)
    if selected is None:
        return []
    name, scorer = selected
    return [Evaluation(name=name, value=scorer())]


# ---------------------------------------------------------------------------
# Check 6 -- entry_path_audit
# ---------------------------------------------------------------------------


def entry_path_audit(*, input, output, **kwargs) -> Evaluation | list[Evaluation]:
    """Score 1.0 when entry_path is correctly honored in the response envelope.

    Applies only to parsed and spec_resubmit goldens; returns [] for raw_text.

    Checks:
    - query_echo.extract_skipped is True (required on every non-raw_text path)
    - On definite/candidates: pipeline_trace carries {step:"extract", ran:false}
    - On no_data: only extract_skipped is checked (no interpretation to read)

    Gate threshold deferred until corpus coverage grows; per-item regression
    is still caught by the flip guard.
    """
    entry_path = (input or {}).get("entry_path", "raw_text")
    if entry_path not in ("parsed", "spec_resubmit"):
        return []

    resp = _parse_response(output)
    if resp is None:
        return Evaluation(
            name="entry_path_audit",
            value=0.0,
            comment="response failed validation",
        )

    root = resp.root
    echo = root.query_echo

    if not echo.extract_skipped:
        return Evaluation(
            name="entry_path_audit",
            value=0.0,
            comment=f"query_echo.extract_skipped is False for entry_path={entry_path!r}",
        )

    # no_data: extract_skipped alone is sufficient (no interpretation pipeline_trace)
    if root.status == "no_data":
        return Evaluation(name="entry_path_audit", value=1.0)

    # definite / candidates: every spec's pipeline_trace must record the skipped extract step
    for spec in _iter_specs(resp):
        trace = spec.resolution.pipeline_trace
        has_skip = any(s.step == "extract" and not s.ran for s in trace)
        if not has_skip:
            return Evaluation(
                name="entry_path_audit",
                value=0.0,
                comment=(
                    f"spec {spec.spec_id}: pipeline_trace missing "
                    "{step:'extract', ran:false}"
                ),
            )

    return Evaluation(name="entry_path_audit", value=1.0)


# ---------------------------------------------------------------------------
# Check 7 -- conjunction_honesty
# ---------------------------------------------------------------------------


def conjunction_honesty(
    *, output, metadata, **kwargs
) -> Evaluation | list[Evaluation]:
    """Score 1.0 when a cross-shape conjunction is honestly signaled in the response.

    Applies only to goldens tagged conjunction == 'cross_shape'; returns []
    for all others.

    Checks:
    - diagnostics.warnings contains CONJUNCTION_CROSS_SHAPE
    - additional_interpretations is a list (not None) — the field being present
      as a list (empty [] or non-empty) confirms cross-shape handling ran

    Gate threshold deferred until corpus coverage grows; per-item regression
    is still caught by the flip guard.
    """
    tags = (metadata or {}).get("tags") or []
    conj_tag = next(
        (t["conjunction"] for t in tags if isinstance(t, dict) and "conjunction" in t),
        None,
    )
    if conj_tag != "cross_shape":
        return []

    resp = _parse_response(output)
    if resp is None:
        return Evaluation(
            name="conjunction_honesty",
            value=0.0,
            comment="response failed validation",
        )

    root = resp.root

    # additional_interpretations is a DefiniteResponse-only field
    if root.status != "definite":
        return Evaluation(
            name="conjunction_honesty",
            value=0.0,
            comment=f"cross_shape golden returned status={root.status!r}; expected definite",
        )

    has_warning = any(
        w.code == "CONJUNCTION_CROSS_SHAPE" for w in root.diagnostics.warnings
    )
    if not has_warning:
        return Evaluation(
            name="conjunction_honesty",
            value=0.0,
            comment="diagnostics.warnings missing CONJUNCTION_CROSS_SHAPE",
        )

    assert isinstance(root, DefiniteResponse)  # guaranteed by status check above
    if root.additional_interpretations is None:
        return Evaluation(
            name="conjunction_honesty",
            value=0.0,
            comment=(
                "additional_interpretations is None; expected a list "
                "(empty [] or non-empty) for a cross_shape conjunction"
            ),
        )

    return Evaluation(name="conjunction_honesty", value=1.0)


# ---------------------------------------------------------------------------
# Check 9 -- axis_classification
# ---------------------------------------------------------------------------


def axis_classification(*, output, expected_output, **kwargs) -> Evaluation | list[Evaluation]:
    """Score 1.0 if all slot axes are in the frozen five and where-slots bind place dcids.

    Emits no Evaluation for non-definite goldens.
    """
    if expected_output.get("expected_status") != "definite":
        return []

    resp = _parse_response(output)
    if resp is None or resp.root.status != "definite":
        return Evaluation(
            name="axis_classification",
            value=0.0,
            comment="response is not definite or failed validation",
        )

    valid_axes = set(get_args(Axis))
    interp = resp.root.interpretation

    for slot in interp.slots:
        if slot.key.axis not in valid_axes:
            return Evaluation(
                name="axis_classification",
                value=0.0,
                comment=f"invalid axis: {slot.key.axis!r} (valid: {valid_axes})",
            )
        if slot.key.axis == "where":
            b = slot.binding
            dcids_to_check: list[str] = []
            if isinstance(b, BindingValue) and b.value.ref is not None:
                dcids_to_check.append(b.value.ref.dcid)
            elif isinstance(b, BindingSet):
                dcids_to_check.extend(v.ref.dcid for v in b.values if v.ref is not None)
            for dcid in dcids_to_check:
                if not _is_place_dcid(dcid):
                    return Evaluation(
                        name="axis_classification",
                        value=0.0,
                        comment=f"where-slot dcid does not look like a place: {dcid!r}",
                    )

    return Evaluation(name="axis_classification", value=1.0)


# ---------------------------------------------------------------------------
# Run-level (aggregate) evaluators
# ---------------------------------------------------------------------------


def _agg_mean(item_name: str, agg_name: str, item_results) -> Evaluation:
    """Return the mean of per-item evaluations named ``item_name``, ignoring None values.

    The resulting Evaluation carries ``agg_name`` as its name.
    """
    values = []
    for r in item_results:
        for ev in r.evaluations:
            if ev.name == item_name and ev.value is not None:
                values.append(float(ev.value))
    if not values:
        # None is the deliberate not-applicable sentinel the gate filters; langfuse's
        # Evaluation types value as numeric, so the type-check is suppressed here.
        return Evaluation(name=agg_name, value=None)  # ty: ignore[invalid-argument-type]
    return Evaluation(name=agg_name, value=sum(values) / len(values))


def agg_interpretation_match_rate(*, item_results, **kwargs) -> Evaluation:
    """Mean of item interpretation_match values, ignoring None (non-definite skips)."""
    return _agg_mean("interpretation_match", "interpretation_match_rate", item_results)


def agg_fabricated_ref_rate(*, item_results, **kwargs) -> Evaluation:
    """Total fabricated / total walked across all items (not a mean of per-item booleans)."""
    fab = 0
    walked = 0
    for r in item_results:
        for ev in r.evaluations:
            if ev.name == "groundedness" and ev.metadata:
                fab += ev.metadata.get("fabricated", 0)
                walked += ev.metadata.get("walked", 0)
    return Evaluation(
        name="fabricated_ref_rate",
        value=(fab / walked if walked else 0.0),
        comment=f"fabricated={fab}, walked={walked}",
    )


def agg_behaviour_match_rate_definite(*, item_results, **kwargs) -> Evaluation:
    """Mean behaviour_match_definite, ignoring None."""
    return _agg_mean(
        "behaviour_match_definite", "behaviour_match_rate_definite", item_results
    )


def agg_behaviour_match_rate_candidates(*, item_results, **kwargs) -> Evaluation:
    """Mean behaviour_match_candidates, ignoring None."""
    return _agg_mean(
        "behaviour_match_candidates", "behaviour_match_rate_candidates", item_results
    )


def agg_behaviour_match_rate_no_data(*, item_results, **kwargs) -> Evaluation:
    """Mean behaviour_match_no_data, ignoring None."""
    return _agg_mean(
        "behaviour_match_no_data", "behaviour_match_rate_no_data", item_results
    )


def agg_materialisation_correct_rate(*, item_results, **kwargs) -> Evaluation:
    """Mean materialisation values, ignoring None (non-definite skips)."""
    return _agg_mean("materialisation", "materialisation_correct_rate", item_results)


def agg_structural_conformance_rate(*, item_results, **kwargs) -> Evaluation:
    """Mean structural_conformance values."""
    return _agg_mean("structural_conformance", "structural_conformance_rate", item_results)


DEFAULT_RUN_EVALUATORS = [
    agg_interpretation_match_rate,
    agg_fabricated_ref_rate,
    agg_behaviour_match_rate_definite,
    agg_behaviour_match_rate_candidates,
    agg_behaviour_match_rate_no_data,
    agg_materialisation_correct_rate,
    agg_structural_conformance_rate,
]
# entry_path_audit and conjunction_honesty are registered in make_item_evaluators
# only — no run-level aggregates, no GATE_THRESHOLDS entries. Too thin for a stable
# aggregate threshold. Per-item regression is still caught: the flip guard compares
# per-item Evaluation scores against the re-frozen per_item baseline and raises on
# any 1.0→0.0 flip. The run-level aggregate + threshold are deferred until corpus
# coverage grows.


def make_item_evaluators(graph: GraphClient) -> list:
    """Build the list of item-level evaluators, injecting the graph client."""
    return [
        structural_conformance,
        make_groundedness(graph),
        interpretation_match,
        make_materialisation(graph),
        behaviour_by_tag,
        entry_path_audit,
        conjunction_honesty,
        axis_classification,
    ]
