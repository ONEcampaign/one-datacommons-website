"""Shared helper functions and threshold constants for the hook pipeline.

``_resolve_confidence``, ``_caveats``, ``_ordered_union``, ``_project_variables``,
and the four threshold constants are used by both registry.py and
materialization.py.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from dc_search.predicate import (
    CONFIDENCE_LEVELS,
    Caveat,
    Confidence,
    DateRange,
    ResolvedVariable,
)
from dc_search.retrieval import StatVarFeatures

from .context import HookContext


def _resolve_confidence(
    *,
    current: Confidence,
    upgrade_to: Confidence | None = None,
    downgrade_to: Confidence | None = None,
) -> Confidence:
    """Monotone update: promote toward ``upgrade_to``, then cap at ``downgrade_to``."""
    rank = CONFIDENCE_LEVELS.index
    new = current
    if upgrade_to is not None and rank(upgrade_to) > rank(new):
        new = upgrade_to
    if downgrade_to is not None and rank(downgrade_to) < rank(new):
        new = downgrade_to
    return new


_RETRIEVAL_QUALITY_THRESHOLD: float = 0.5
_WEAK_RETRIEVAL_TOPIC_DUMP_THRESHOLD: float = 0.7
_CRS_DAC_SV_CAP = 200
_TOPIC_SV_CAP = 200


def _caveats(*extra: Caveat, base: list[Caveat] | None = None) -> list[Caveat]:
    """Build a caveat list, de-duplicating as we go."""
    seen: set[str] = set(base or [])
    result: list[Caveat] = list(base or [])
    for c in extra:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


def _ordered_union(seqs: Iterable[Iterable[str]]) -> list[str]:
    """Concatenate sequences, preserving first-appearance order, dedup-aware."""
    seen: set[str] = set()
    out: list[str] = []
    for seq in seqs:
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
    return out


def _project_variables(
    sv_set: list[str],
    feat_by_dcid: Mapping[str, StatVarFeatures],
    ctx: HookContext,
) -> list[ResolvedVariable]:
    """Project each DCID in ``sv_set`` to an enriched ``ResolvedVariable``.

    Features are looked up in ``feat_by_dcid`` (missing → fields left None);
    availability and date_range are read from ``ctx``.  Availability is
    tri-state: None when no place resolved or availability uncomputed, else
    membership in ``ctx.place_availability``.

    Callers differ only in how they assemble ``feat_by_dcid``:
    ``materialization._build_variables`` indexes ``ctx.raw_candidates``;
    ``registry``'s CRS recovery path passes a freshly-fetched feature dict for
    DCIDs that are absent from ``raw_candidates``.
    """
    out: list[ResolvedVariable] = []
    for dcid in sv_set:
        f = feat_by_dcid.get(dcid)
        if not ctx.place_dcids or ctx.place_availability is None:
            avail = None
        else:
            avail = dcid in ctx.place_availability
        rng = ctx.dcid_to_date_range.get(dcid)
        date_range = DateRange(earliest=rng[0], latest=rng[1]) if rng else None
        out.append(
            ResolvedVariable(
                dcid=dcid,
                name=f.name if f else None,
                description=f.description if f else None,
                unit=(f.unit[0] if f and f.unit else None),
                measured_property=(f.measured_property[0] if f and f.measured_property else None),
                population_type=(f.population_type[0] if f and f.population_type else None),
                stat_type=(f.stat_type[0] if f and f.stat_type else None),
                measurement_denominator=(
                    f.measurement_denominator[0] if f and f.measurement_denominator else None
                ),
                score=ctx.retrieval_scores.get(dcid),
                matched_sentence=ctx.dcid_to_sentence.get(dcid),
                available_at_place=avail,
                date_range=date_range,
            )
        )
    return out
