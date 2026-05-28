"""Diff two ``perf_harness.py`` JSON outputs.

Usage::

    .venv/bin/python scripts/perf_diff.py baseline.json after.json

Prints a per-query table (cold p50/p95 + warm p50/p95, baseline → after, delta
and percent change) followed by an overall row. Negative deltas = faster.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


def _fmt_delta(base: float | None, after: float | None) -> str:
    if base is None or after is None:
        return "    -    "
    d = after - base
    pct = (d / base * 100.0) if base else 0.0
    sign = "+" if d >= 0 else ""
    return f"{sign}{d:6.3f}s ({sign}{pct:5.1f}%)"


def _row(label: str, base_stats: dict, after_stats: dict) -> str:
    bc50 = base_stats["cold"]["p50"]
    ac50 = after_stats["cold"]["p50"]
    bc95 = base_stats["cold"]["p95"]
    ac95 = after_stats["cold"]["p95"]
    bw50 = base_stats["warm"]["p50"]
    aw50 = after_stats["warm"]["p50"]
    bw95 = base_stats["warm"]["p95"]
    aw95 = after_stats["warm"]["p95"]
    return (
        f"{label:<46} "
        f"cold p50 {bc50!s:>6}→{ac50!s:>6} {_fmt_delta(bc50, ac50)}   "
        f"cold p95 {bc95!s:>6}→{ac95!s:>6} {_fmt_delta(bc95, ac95)}   "
        f"warm p50 {bw50!s:>6}→{aw50!s:>6} {_fmt_delta(bw50, aw50)}   "
        f"warm p95 {bw95!s:>6}→{aw95!s:>6} {_fmt_delta(bw95, aw95)}"
    )


def _per_query_stats(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {q["query"]: {"cold": q["cold"]["stats"], "warm": q["warm"]["stats"]} for q in report["queries"]}


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: perf_diff.py BASELINE.json AFTER.json", file=sys.stderr)
        return 2

    base = json.loads(Path(argv[0]).read_text())
    after = json.loads(Path(argv[1]).read_text())

    print(f"baseline label={base.get('label')!r}  after label={after.get('label')!r}")
    print(f"baseline duration={base.get('duration_s')}s  after duration={after.get('duration_s')}s")
    print()

    base_q = _per_query_stats(base)
    after_q = _per_query_stats(after)

    for query in base_q:
        if query not in after_q:
            print(f"  (missing in after) {query}")
            continue
        print(_row(query[:46], base_q[query], after_q[query]))

    extra = set(after_q) - set(base_q)
    for q in extra:
        print(f"  (extra in after) {q}")

    print()
    print(
        _row(
            "OVERALL",
            {"cold": base["overall"]["cold"], "warm": base["overall"]["warm"]},
            {"cold": after["overall"]["cold"], "warm": after["overall"]["warm"]},
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
