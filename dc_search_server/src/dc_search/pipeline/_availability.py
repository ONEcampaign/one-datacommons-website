"""Availability re-rank helpers — imported by _run so patches intercept lookups."""

from __future__ import annotations

from dc_search import retrieval
from dc_search.hooks import HookContext  # noqa: F401 — imported for re-use by callers


def _availability_sort_key(dcid: str, union_avail: frozenset[str]) -> int:
    """Return 0 for SVs in availability set (higher priority), 1 otherwise."""
    return 0 if dcid in union_avail else 1


def _resolve_union_availability(
    place_dcids: list[str],
    candidate_sv_dcids: tuple[str, ...] = (),
) -> frozenset[str]:
    """Union the availability sets for already-resolved place DCIDs.

    Candidate path (known SVs): hybrid — custom-DC vars resolved via the
    coverage map's {E,V} presence, base-DC vars (map-absent) via a targeted
    live observation fetch, unioned. Map-absent + base-absent vars fail-open
    (not in either set, so _apply_availability_filter's empty-intersection
    fallback preserves them).

    Topic path (no candidate SVs): falls back to full-inventory batch.
    """
    if not place_dcids:
        return frozenset()

    if candidate_sv_dcids:
        # Fetch precomputed coverage map for the candidate set + resolved places.
        cov = retrieval.variable_date_coverage(
            variable_dcids=candidate_sv_dcids,
            entity_dcids=tuple(place_dcids),
        )

        # Custom vars: present in the coverage map at any of the resolved places.
        custom_present: set[str] = {
            v for v in candidate_sv_dcids if any((v, e) in cov.entity_ranges for e in place_dcids)
        }

        # Base-DC vars: map-absent (not in cov.envelopes) -> live obs check.
        base_candidates = tuple(v for v in candidate_sv_dcids if v not in cov.envelopes)
        base_present: frozenset[str] = (
            retrieval.presence_for_entities(
                variable_dcids=base_candidates,
                entity_dcids=tuple(place_dcids),
            )
            if base_candidates
            else frozenset()
        )

        return frozenset(custom_present | base_present)

    batch = retrieval.variables_for_entities_batch(entity_dcids=tuple(place_dcids))
    available: set[str] = set()
    for svs in batch.values():
        available.update(svs)
    return frozenset(available)


def _resolve_union_availability_checked(
    place_dcids: list[str],
    candidate_sv_dcids: tuple[str, ...] = (),
) -> tuple[frozenset[str], bool]:
    """``_resolve_union_availability`` plus a fail-open degraded flag.

    Runs as the ``asyncio.to_thread`` target so it can read retrieval's per-context
    degraded flag in the SAME thread the coverage/presence helpers ran on (a
    ContextVar mutation inside a thread does not propagate back to the caller's
    context, so the flag must be captured here and returned).
    """
    retrieval.reset_dc_call_degraded()
    avail = _resolve_union_availability(place_dcids, candidate_sv_dcids)
    return avail, retrieval.dc_call_was_degraded()


def _resolve_union_availability_with_ranges(
    place_dcids: list[str],
    candidate_sv_dcids: tuple[str, ...] = (),
) -> tuple[frozenset[str], dict[str, tuple[str | None, str | None]], bool]:
    """Like ``_resolve_union_availability_checked`` but also returns per-DCID date ranges.

    For custom-DC vars that appear in the coverage map's ``entity_ranges``, unions
    the ``(earliest, latest)`` strings across ``place_dcids`` via string-comparison
    min/max (ISO-style: lexicographic ordering preserves temporal ordering).
    For base-DC vars (absent from ``cov.envelopes``), issues a facet-select
    observation query to get the true place-specific span, and merges those
    ranges into the result.

    Returns:
        ``(availability_frozenset, dcid_to_range, degraded)`` — the range dict maps
        SV DCID to ``(earliest, latest)``; absent entries mean no range known.
    """
    retrieval.reset_dc_call_degraded()

    ranges: dict[str, tuple[str | None, str | None]] = {}

    if not place_dcids or not candidate_sv_dcids:
        avail = _resolve_union_availability(place_dcids, candidate_sv_dcids)
        return avail, ranges, retrieval.dc_call_was_degraded()

    cov = retrieval.variable_date_coverage(
        variable_dcids=candidate_sv_dcids,
        entity_dcids=tuple(place_dcids),
    )

    # Custom vars: union entity_ranges over all resolved places.
    custom_present: set[str] = set()
    for v in candidate_sv_dcids:
        if v not in cov.envelopes:
            continue  # base-DC var — skip range extraction
        lo: str | None = None
        hi: str | None = None
        present_at_any = False
        for e in place_dcids:
            er = cov.entity_ranges.get((v, e))
            if er is None:
                continue
            present_at_any = True
            er_lo, er_hi = er
            if er_lo is not None:
                lo = er_lo if (lo is None or er_lo < lo) else lo
            if er_hi is not None:
                hi = er_hi if (hi is None or er_hi > hi) else hi
        if present_at_any:
            custom_present.add(v)
            ranges[v] = (lo, hi)

    # Base-DC vars: facet-select observation query for presence + true date spans.
    base_candidates = tuple(v for v in candidate_sv_dcids if v not in cov.envelopes)
    base_present, base_ranges = (
        retrieval.observation_facet_ranges(
            variable_dcids=base_candidates,
            entity_dcids=tuple(place_dcids),
        )
        if base_candidates
        else (frozenset(), {})
    )
    ranges.update(base_ranges)

    avail = frozenset(custom_present | base_present)
    return avail, ranges, retrieval.dc_call_was_degraded()
