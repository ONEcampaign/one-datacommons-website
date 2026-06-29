"""QRE conjunction combiner: pure, zero-graph region assembly and grouping.

No graph or LLM imports — operates only on already-grounded RegionResult / Spec objects.

Public surface:
  Warning code constants (CONJUNCTION_CROSS_SHAPE, etc.) with pinned severities and messages.
  assemble_region(region, …) → ResolveResponse   (single-region primitive; DRY with N=1 path)
  combine_regions(regions, …) → ResolveResponse  (N≥2 grouping combiner)

Pure helper functions (unit-tested in test_conjoin.py):
  five_tuple_key, collapse_same_shape, select_primary, cross_shape_present,
  build_conjunction_warnings.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from qre.engine.assemble import (
    assemble_candidates,
    assemble_definite,
    assemble_no_data,
    make_diagnostics,
    make_query_echo,
    now_ms,
)
from qre.models import (
    Spec,
    Warning,
)

if TYPE_CHECKING:
    from qre.engine.regions import RegionResult
    from qre.models import ResolveResponse

# ---------------------------------------------------------------------------
# Warning code constants — severities and message templates are PINNED
# ---------------------------------------------------------------------------

CONJUNCTION_CROSS_SHAPE = "CONJUNCTION_CROSS_SHAPE"
CONJUNCTION_PART_AMBIGUOUS = "CONJUNCTION_PART_AMBIGUOUS"
CONJUNCTION_PART_NO_DATA = "CONJUNCTION_PART_NO_DATA"
VARIABLES_CLAMPED = "VARIABLES_CLAMPED"


def _cross_shape_msg(variable_texts: list[str]) -> str:
    return f"Distinct measures conjoined: {'; '.join(variable_texts)}."


def _part_ambiguous_msg(variable_text: str) -> str:
    return f"Variable '{variable_text}' was ambiguous."


def _part_no_data_msg(variable_text: str) -> str:
    return f"Variable '{variable_text}' returned no data."


# ---------------------------------------------------------------------------
# Pure combiner helpers
# ---------------------------------------------------------------------------


def five_tuple_key(spec: Spec) -> tuple[str, str, str, str | None, str | None]:
    """Extract the five-tuple structural key from a Spec.

    The five-tuple is (population_type, measured_property, stat_type,
    measurement_qualifier, measurement_denominator) — the same dimensions
    that define a shape_id group.
    """
    sh = spec.shape
    return (
        sh.population_type.dcid,
        sh.measured_property.dcid,
        sh.stat_type.dcid,
        sh.measurement_qualifier.dcid if sh.measurement_qualifier else None,
        sh.measurement_denominator.dcid if sh.measurement_denominator else None,
    )


def _dedupe_by(items: list, *, key) -> list:
    """Return items with duplicates removed, preserving order.

    key is a callable that returns a hashable comparison key.
    """
    seen: set = set()
    result = []
    for item in items:
        k = key(item)
        if k not in seen:
            seen.add(k)
            result.append(item)
    return result


def _slot_key_t(slot) -> tuple[str, str | None]:
    """Return the (axis, property_dcid) identity tuple for a slot."""
    return (slot.key.axis, slot.key.property.dcid if slot.key.property else None)


def collapse_same_shape(
    definite: list["RegionResult"],
) -> tuple[list["RegionResult"], list[str]]:
    """Tier-1 same-shape collapse: merge regions sharing the same five-tuple.

    Groups definite regions by five_tuple_key(r.spec). For each group:
      - size 1: pass through unchanged.
      - size ≥ 2, exactly ONE slot-key (axis, property.dcid) differs and it is
        a "value" binding in every member: merge to one definite region whose
        differing slot becomes a BindingSet. Reuse assemble.build_spec to recompute
        spec_id and slot_filters. Sets earliest_index = min(group).
      - size ≥ 2, > 1 slot differs (or differing slot is not a clean "value"):
        keep the FIRST member as the effective definite region; every other member's
        variable_text goes into residual_ambiguous_texts (SAME_SHAPE_MULTISLOT rule).

    Returns:
        (effective_definite, residual_ambiguous_texts) where:
          - effective_definite: one RegionResult per group
          - residual_ambiguous_texts: variable_texts of dropped non-first members
    """
    import dataclasses

    from qre.engine.assemble import build_spec
    from qre.models import BindingSet, BindingValue, SlotValue

    if not definite:
        return [], []

    # Group by five-tuple
    groups: dict[tuple, list["RegionResult"]] = {}
    for r in definite:
        k = five_tuple_key(r.spec)
        groups.setdefault(k, []).append(r)

    effective: list["RegionResult"] = []
    residual_texts: list[str] = []

    for group in groups.values():
        if len(group) == 1:
            effective.append(group[0])
            continue

        # Find differing slots: compare all members against the first.
        base_spec = group[0].spec
        base_slots = {_slot_key_t(s): s for s in base_spec.slots}

        differing_keys: set[tuple[str, str | None]] = set()
        for r in group[1:]:
            for s in r.spec.slots:
                k = _slot_key_t(s)
                base_s = base_slots.get(k)
                if base_s is None:
                    differing_keys.add(k)
                    continue
                # Compare binding kind and value dcids
                if s.binding.kind != base_s.binding.kind:
                    differing_keys.add(k)
                elif s.binding.kind == "value" and base_s.binding.kind == "value":
                    s_ref = s.binding.value.ref
                    b_ref = base_s.binding.value.ref
                    if (s_ref.dcid if s_ref else None) != (b_ref.dcid if b_ref else None):
                        differing_keys.add(k)
                elif s.binding.kind == "set" and base_s.binding.kind == "set":
                    s_dcids = frozenset(v.ref.dcid for v in s.binding.values if v.ref)
                    b_dcids = frozenset(v.ref.dcid for v in base_s.binding.values if v.ref)
                    if s_dcids != b_dcids:
                        differing_keys.add(k)

        if len(differing_keys) != 1:
            # SAME_SHAPE_MULTISLOT rule: keep first, emit residuals
            effective.append(group[0])
            for r in group[1:]:
                residual_texts.append(r.variable_text)
            continue

        # Exactly one differing slot — check it is a "value" binding in every member
        diff_key = next(iter(differing_keys))
        all_value = True
        for r in group:
            s = next((sl for sl in r.spec.slots if _slot_key_t(sl) == diff_key), None)
            if s is None or s.binding.kind != "value":
                all_value = False
                break

        if not all_value:
            # Differing slot is not a clean value in every member: residual rule
            effective.append(group[0])
            for r in group[1:]:
                residual_texts.append(r.variable_text)
            continue

        # Merge: collect the differing slot's value refs from all members, build BindingSet
        diff_refs: list = []
        seen_dcids: set[str] = set()
        for r in group:
            s = next(sl for sl in r.spec.slots if _slot_key_t(sl) == diff_key)
            # all_value check above guarantees every diff-slot is BindingValue here.
            assert isinstance(s.binding, BindingValue)
            ref = s.binding.value.ref
            if ref and ref.dcid not in seen_dcids:
                seen_dcids.add(ref.dcid)
                diff_refs.append(ref)

        # Need at least 2 distinct refs for a valid BindingSet
        if len(diff_refs) < 2:
            effective.append(group[0])
            for r in group[1:]:
                residual_texts.append(r.variable_text)
            continue


        # Build merged slots: all slots unchanged except the differing one becomes a BindingSet
        merged_slots = []
        for sl in base_spec.slots:
            if _slot_key_t(sl) == diff_key:
                set_values = [SlotValue(ref=ref, value_kind="entity") for ref in diff_refs]
                merged_slots.append(
                    sl.model_copy(update={"binding": BindingSet(values=set_values)})
                )
            else:
                merged_slots.append(sl)

        # Union of stat_vars across group members, deduped by ref.dcid
        all_stat_vars = []
        seen_sv_dcids: set[str] = set()
        for r in group:
            for sv in r.spec.stat_vars:
                if sv.ref.dcid not in seen_sv_dcids:
                    seen_sv_dcids.add(sv.ref.dcid)
                    all_stat_vars.append(sv)

        merged_variable_text = " and ".join(r.variable_text for r in group)
        earliest = min(r.earliest_index for r in group)
        merged_warnings = tuple(
            w for r in group for w in r.warnings
        )

        # Downgrade CoverageExact: the merged spec covers stat_vars from multiple
        # members, so the first member's exact observation_count no longer applies.
        # conjoin.py is pure (no graph), so an accurate recount is impossible here.
        from qre.models import CoverageBare, CoverageBreadth  # noqa: PLC0415
        base_cov = base_spec.coverage
        if base_cov.kind == "exact":
            if base_cov.dimensions:
                merged_coverage = CoverageBreadth(
                    has_data=base_cov.has_data,
                    dimensions=base_cov.dimensions,
                    window=base_cov.window,
                )
            else:
                merged_coverage = CoverageBare(
                    has_data=base_cov.has_data,
                    window=base_cov.window,
                )
        else:
            merged_coverage = base_cov

        merged_spec = build_spec(
            shape=base_spec.shape,
            slots=merged_slots,
            stat_vars=all_stat_vars,
            entities=base_spec.entities,
            coverage=merged_coverage,
            pipeline_trace=list(base_spec.resolution.pipeline_trace),
            timing_by_step={},
            variable_text=merged_variable_text,
        )
        merged_timing = dict(group[0].timing_by_step)

        merged_region = dataclasses.replace(
            group[0],
            variable_text=merged_variable_text,
            specs=(merged_spec,),
            warnings=merged_warnings,
            timing_by_step=merged_timing,
            earliest_index=earliest,
        )
        effective.append(merged_region)

    return effective, residual_texts


def select_primary(
    effective: list["RegionResult"],
) -> tuple["RegionResult", list["RegionResult"]]:
    """Select the primary region and extras from effective regions.

    Primary = first (by earliest_index) definite region; if none are definite,
    primary = effective[0].

    Returns:
        (primary, extras) where extras are in earliest_index order.
    """
    definites = [r for r in effective if r.status == "definite"]
    if definites:
        primary = min(definites, key=lambda r: r.earliest_index)
    else:
        primary = effective[0]
    extras = [r for r in effective if r is not primary]
    extras.sort(key=lambda r: r.earliest_index)
    return primary, extras


def cross_shape_present(
    primary: "RegionResult",
    extras: list["RegionResult"],
) -> bool:
    """True if any extra is non-definite, or any definite extra has a distinct five-tuple.

    After Tier-1 collapse, definite extras necessarily have distinct five-tuples,
    so this returns True whenever extras exist — but the explicit predicate keeps
    the same-shape residual case (no extras) correct.
    """
    if not extras:
        return False
    primary_key = five_tuple_key(primary.spec) if primary.status == "definite" else None
    for extra in extras:
        if extra.status != "definite":
            return True
        if primary_key is None or five_tuple_key(extra.spec) != primary_key:
            return True
    return False


def build_conjunction_warnings(
    primary: "RegionResult",
    extras: list["RegionResult"],
    variable_texts: list[str],
) -> list[Warning]:
    """Build conjunction-specific warnings for the multi-region path.

    Appends CONJUNCTION_CROSS_SHAPE when cross-shape is detected, then
    per-extra CONJUNCTION_PART_AMBIGUOUS / CONJUNCTION_PART_NO_DATA.
    Definite extras produce no warning (they ride additional_interpretations).
    """
    result: list[Warning] = []
    if cross_shape_present(primary, extras):
        result.append(Warning(
            code=CONJUNCTION_CROSS_SHAPE,
            severity="info",
            message=_cross_shape_msg(variable_texts),
        ))
    for extra in extras:
        if extra.status == "candidates":
            result.append(Warning(
                code=CONJUNCTION_PART_AMBIGUOUS,
                severity="warn",
                message=_part_ambiguous_msg(extra.variable_text),
            ))
        elif extra.status == "no_data":
            result.append(Warning(
                code=CONJUNCTION_PART_NO_DATA,
                severity="warn",
                message=_part_no_data_msg(extra.variable_text),
            ))
    return result


def _spec_id_dedupe(
    specs: list[Spec],
    *,
    against: Spec,
) -> list[Spec]:
    """Return specs whose spec_id differs from `against`, preserving order."""
    seen = {against.spec_id}
    result = []
    for s in specs:
        if s.spec_id not in seen:
            seen.add(s.spec_id)
            result.append(s)
    return result


# ---------------------------------------------------------------------------
# assemble_region: single-region → ResolveResponse (DRY primitive)
# ---------------------------------------------------------------------------


def assemble_region(
    region: "RegionResult",
    *,
    query: str,
    variable_texts: list[str],
    extra_warnings: list[Warning],
    start_ms: int,
    engine_build: str,
    include_sentence: bool,
    max_candidates: int,
) -> "ResolveResponse":
    """Assemble a single RegionResult into a ResolveResponse.

    Used by both the N=1 orchestrator bypass and the combiner's full-collapse
    safety net so the two paths produce identical output.

    n_measures = len(variable_texts) — when >= 2 the C5 sentence suffix applies.
    """

    warnings = _dedupe_by(
        list(region.warnings) + extra_warnings,
        key=lambda w: (w.code, w.severity, w.message),
    )
    echo = make_query_echo(query, variable_texts, extract_skipped=False)
    diag = make_diagnostics(engine_build, warnings, region.timing_by_step, now_ms() - start_ms)
    k = len(variable_texts)

    if region.status == "definite":
        return assemble_definite(
            region.spec, echo, diag,
            additional_interpretations=None,
            include_sentence=include_sentence,
            n_measures=k,
        )
    if region.status == "candidates":
        return assemble_candidates(
            list(region.specs), echo, diag,
            max_candidates=max_candidates,
            include_sentence=include_sentence,
            n_measures=k,
        )
    return assemble_no_data(
        region.no_data_reason, echo, diag,
        include_sentence=include_sentence,
        n_measures=k,
    )


# ---------------------------------------------------------------------------
# combine_regions: N≥2 grouping combiner
# ---------------------------------------------------------------------------


def combine_regions(
    regions: list["RegionResult"],
    *,
    query: str,
    variable_texts: list[str],
    extra_warnings: list[Warning],
    start_ms: int,
    engine_build: str,
    include_sentence: bool,
    max_candidates: int,
) -> "ResolveResponse":
    """Combine N≥2 RegionResults into a single ResolveResponse.

    Tiers:
      1. Same-shape collapse: regions sharing a five-tuple merge to one (single-slot set).
      2. Cross-shape conjunction: primary in interpretation, other definites in
         additional_interpretations, non-definites surfaced as warnings.
      3. Full mixed-outcome matrix (primary candidates / no_data).
    """
    definite = [r for r in regions if r.status == "definite"]
    others = [r for r in regions if r.status != "definite"]

    collapsed, residual_texts = collapse_same_shape(definite)
    effective = sorted(collapsed + others, key=lambda r: r.earliest_index)

    residual_warnings: list[Warning] = [
        Warning(
            code=CONJUNCTION_PART_AMBIGUOUS,
            severity="warn",
            message=_part_ambiguous_msg(t),
        )
        for t in residual_texts
    ]

    # Safety net: full same-shape collapse → single effective region.
    # This is NOT the N=1 bypass path; N=1 bypasses the combiner entirely.
    if len(effective) == 1:
        return assemble_region(
            effective[0],
            query=query,
            variable_texts=variable_texts,
            extra_warnings=extra_warnings + residual_warnings,
            start_ms=start_ms,
            engine_build=engine_build,
            include_sentence=include_sentence,
            max_candidates=max_candidates,
        )

    # Multi-region path
    primary, extras = select_primary(effective)

    base_warnings = _dedupe_by(
        [w for r in regions for w in r.warnings],
        key=lambda w: (w.code, w.severity, w.message),
    )
    conj_warnings = build_conjunction_warnings(primary, extras, variable_texts)
    warnings = base_warnings + extra_warnings + residual_warnings + conj_warnings

    echo = make_query_echo(query, variable_texts, extract_skipped=False)
    diag = make_diagnostics(
        engine_build, warnings, primary.timing_by_step, now_ms() - start_ms
    )
    k = len(variable_texts)

    if primary.status == "definite":
        other_definite = _spec_id_dedupe(
            [e.spec for e in extras if e.status == "definite"],
            against=primary.spec,
        )
        if other_definite:
            additional = other_definite
        elif cross_shape_present(primary, extras):
            additional = []
        else:
            additional = None

        return assemble_definite(
            primary.spec, echo, diag,
            additional_interpretations=additional,
            include_sentence=include_sentence,
            n_measures=k,
        )

    if primary.status == "candidates":
        # Contract bars additional_interpretations on candidates responses.
        return assemble_candidates(
            list(primary.specs), echo, diag,
            max_candidates=max_candidates,
            include_sentence=include_sentence,
            n_measures=k,
        )

    # Primary is no_data; conjunction acknowledged via warnings.
    return assemble_no_data(
        primary.no_data_reason, echo, diag,
        include_sentence=include_sentence,
        n_measures=k,
    )
