"""Pure date-range helper functions for DateFilterHook."""

from __future__ import annotations

from dc_search.retrieval import DateCoverage


def _year(date_str: str | None) -> int | None:
    """Leading 4-digit year of an ISO date string, or None.

    Compares mixed-granularity dates ("2015", "2015-03", "2015-03-01") at year
    granularity per the brief. Returns None for None/empty/unparseable input.
    """
    if not date_str:
        return None
    head = date_str[:4]
    return int(head) if head.isdigit() else None


def _overlaps(
    cov_min: str | None,
    cov_max: str | None,
    win_start: str | None,
    win_end: str | None,
) -> bool:
    """True if coverage [cov_min, cov_max] overlaps window [win_start, win_end].

    Year granularity; open bounds (None) are treated as -inf / +inf. With no
    coverage evidence on either side, returns True (caller fails open). Overlap
    rule: cov_min <= win_end AND cov_max >= win_start.
    """
    cmin, cmax = _year(cov_min), _year(cov_max)
    wstart, wend = _year(win_start), _year(win_end)
    # No positive coverage evidence -> caller should keep (fail-open).
    if cmin is None and cmax is None:
        return True
    # Open bounds (None) widen to ±inf so a missing edge never excludes overlap.
    lo = cmin if cmin is not None else float("-inf")
    hi = cmax if cmax is not None else float("inf")
    ws = wstart if wstart is not None else float("-inf")
    we = wend if wend is not None else float("inf")
    return lo <= we and hi >= ws


def _union_range(
    a: tuple[str | None, str | None] | None,
    b: tuple[str | None, str | None],
) -> tuple[str | None, str | None]:
    """Union two (min, max) ranges at string granularity; None bounds widen."""
    if a is None:
        return b
    amin, amax = a
    bmin, bmax = b
    lo = min(x for x in (amin, bmin) if x) if (amin or bmin) else None
    hi = max(x for x in (amax, bmax) if x) if (amax or bmax) else None
    return (lo, hi)


def _range_for(
    v: str,
    cov: DateCoverage,
    base_ranges: dict[str, tuple[str | None, str | None]],
    place_dcids: tuple[str, ...],
) -> tuple[str, tuple[str | None, str | None] | None]:
    """Effective coverage verdict for var v as a 3-state result.

    A plain (min,max)|None return is insufficient because ``None`` would have to
    mean two opposite things. The three states disambiguate:
      ("keep",  None)     -> var absent from the map entirely (base-DC with no
                             evidence) -> fail-open keep. Absence != miss.
      ("drop",  None)     -> var IS in the map ({V} present) but has no {E,V}
                             at the resolved places -> positive evidence it has
                             no data there -> clear miss -> drop.
      ("range", (lo, hi)) -> a concrete range to test against the window.
    """
    if v in cov.envelopes:
        if place_dcids:
            pairs = [cov.entity_ranges[(v, e)] for e in place_dcids if (v, e) in cov.entity_ranges]
            if not pairs:
                return ("drop", None)  # custom, no data at these places
            lo = min((p[0] for p in pairs if p[0]), default=None)
            hi = max((p[1] for p in pairs if p[1]), default=None)
            return ("range", (lo, hi))
        return ("range", cov.envelopes[v])
    rng = base_ranges.get(v)
    if rng is None:
        return ("keep", None)  # base-DC, no evidence -> fail-open
    return ("range", rng)
