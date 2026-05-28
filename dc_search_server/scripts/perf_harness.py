"""Latency harness for the dc_search pipeline.

Runs ``run_default`` against a canonical query set, optionally clearing all
process-level caches between iterations so cold-cache timings are isolated from
warm-cache ones. Designed for before/after comparison of the perf-review fixes;
see ``.claude/perf-review-2026-05-28.md`` for the findings this targets.

Usage::

    # default 5 cold + 3 warm iterations per query, write JSON to disk
    .venv/bin/python scripts/perf_harness.py --output baseline.json --label baseline

    # quick smoke (1 cold + 1 warm per query)
    .venv/bin/python scripts/perf_harness.py --quick --output smoke.json

    # custom query set
    .venv/bin/python scripts/perf_harness.py --queries "population of france,malaria grants to kenya"

Two JSON files (baseline + after) can be diffed with ``scripts/perf_diff.py``.

Requires a running proxy on ``$DC_API_URL`` (default ``http://localhost:8081/v2``)
and ``GEMINI_API_KEY`` in env / ``.env``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
import traceback
import types
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Make ``src`` importable when running from the package root.
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# load_dotenv before importing the pipeline — the genai client reads GEMINI_API_KEY
# from os.environ at first call, and config.load_dotenv only runs on config access.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from dc_search import retrieval  # noqa: E402
from dc_search.pipeline import run_default  # noqa: E402

# ---------------------------------------------------------------------------
# Canonical query set — covers the five perf-review findings
# ---------------------------------------------------------------------------
# Q1: basic single-var single-place — exercises F1 (rerank + fetch parallelism)
# Q2: WHO single-var single-place — F1 + F5 (constraint-prop batching)
# Q3: CRS_DAC fully-bound single-predicate — F1 + F4 (vgroups pre-warm) + F5
# Q4: CRS_DAC contained-in set-recipient — F1 + F2 (place_names) + F3 (set_recipient pre-warm) + F4 + F5
# Q5: contained-in non-CRS_DAC — F1 + F2
_DEFAULT_QUERIES: tuple[str, ...] = (
    "population of france",
    "deaths from malaria in kenya",
    "malaria ODA grants to kenya",
    "malaria ODA grants to african countries",
    "healthcare spending in african countries",
)

# Cache attributes on the retrieval package — every underscore-prefixed name
# ending in ``_cache``/``_cache_lru`` whose value has a ``clear()`` method.
# The ``not isinstance(..., ModuleType)`` guard skips the ``_cache`` submodule itself.
_CACHE_ATTRS: tuple[str, ...] = tuple(
    sorted(
        name
        for name in dir(retrieval)
        if name.startswith("_")
        and (name.endswith("_cache") or name.endswith("_cache_lru"))
        and not isinstance(getattr(retrieval, name, None), types.ModuleType)
        and callable(getattr(getattr(retrieval, name, None), "clear", None))
    )
)


def _clear_all_caches() -> None:
    """Drop every process-level LRU so the next pipeline run is cold."""
    for attr in _CACHE_ATTRS:
        cache = getattr(retrieval, attr, None)
        if cache is not None and hasattr(cache, "clear"):
            cache.clear()


def _summary(samples: list[float]) -> dict[str, float | None]:
    """Compact stats block for a sample list. ``None`` fields when ``samples`` empty."""
    if not samples:
        return {"n": 0, "p50": None, "p95": None, "mean": None, "min": None, "max": None}
    s = sorted(samples)
    n = len(s)
    p50 = statistics.median(s)
    # ``statistics.quantiles`` with n=20 yields 5%-step quantiles; index 18 ≈ p95.
    if n >= 2:
        p95 = statistics.quantiles(s, n=20, method="inclusive")[18]
    else:
        p95 = s[-1]
    return {
        "n": n,
        "p50": round(p50, 3),
        "p95": round(p95, 3),
        "mean": round(statistics.fmean(s), 3),
        "min": round(min(s), 3),
        "max": round(max(s), 3),
    }


async def _time_one(query: str) -> dict[str, Any]:
    """Run ``run_default(query)`` once; return wallclock + pipeline-reported stats."""
    t0 = time.perf_counter()
    try:
        result = await run_default(query)
        wall = time.perf_counter() - t0
        return {
            "wall_s": round(wall, 3),
            "elapsed_s": round(result.elapsed_s, 3),
            "terminated_by": result.terminated_by,
            "n_candidates": result.n_candidates,
            "n_shapes": result.n_shapes,
            "n_answers": len(result.answers),
            "llm_steps": [
                {
                    "step": u.step,
                    "in_tok": u.input_tokens,
                    "out_tok": u.output_tokens,
                    "cached_in_tok": u.cached_input_tokens,
                    "latency_s": round(u.latency_s, 3) if u.latency_s is not None else None,
                }
                for u in result.llm_usage
            ],
        }
    except Exception as exc:
        wall = time.perf_counter() - t0
        return {
            "wall_s": round(wall, 3),
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(limit=8),
        }


async def _bench_query(
    query: str, *, cold_iters: int, warm_iters: int
) -> dict[str, Any]:
    """Bench one query: ``cold_iters`` runs with caches cleared first, then
    ``warm_iters`` runs reusing the warmed caches from the final cold run.

    Cold p50 reflects fresh-process latency; warm p50 reflects hot-cache latency.
    """
    cold_runs: list[dict[str, Any]] = []
    for _ in range(cold_iters):
        _clear_all_caches()
        cold_runs.append(await _time_one(query))

    warm_runs: list[dict[str, Any]] = []
    for _ in range(warm_iters):
        warm_runs.append(await _time_one(query))

    cold_walls = [r["wall_s"] for r in cold_runs if "error" not in r]
    warm_walls = [r["wall_s"] for r in warm_runs if "error" not in r]
    errors = [
        {"phase": phase, "error": r["error"]}
        for phase, runs in (("cold", cold_runs), ("warm", warm_runs))
        for r in runs
        if "error" in r
    ]

    return {
        "query": query,
        "cold": {"runs": cold_runs, "stats": _summary(cold_walls)},
        "warm": {"runs": warm_runs, "stats": _summary(warm_walls)},
        "errors": errors,
    }


async def _run(queries: list[str], *, cold_iters: int, warm_iters: int) -> dict[str, Any]:
    started = time.time()
    per_query: list[dict[str, Any]] = []
    for q in queries:
        per_query.append(await _bench_query(q, cold_iters=cold_iters, warm_iters=warm_iters))

    all_cold = [w for r in per_query for w in (r["cold"]["runs"]) if "error" not in w]
    all_warm = [w for r in per_query for w in (r["warm"]["runs"]) if "error" not in w]

    return {
        "started_at": started,
        "ended_at": time.time(),
        "duration_s": round(time.time() - started, 3),
        "cold_iters": cold_iters,
        "warm_iters": warm_iters,
        "queries": per_query,
        "overall": {
            "cold": _summary([r["wall_s"] for r in all_cold]),
            "warm": _summary([r["wall_s"] for r in all_warm]),
        },
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--queries",
        type=str,
        default=None,
        help="Comma-separated query list. Defaults to the 5-query canonical set.",
    )
    p.add_argument("--cold-iters", type=int, default=5, help="Cold-cache iterations per query (default 5).")
    p.add_argument("--warm-iters", type=int, default=3, help="Warm-cache iterations per query (default 3).")
    p.add_argument("--quick", action="store_true", help="Shortcut: 1 cold + 1 warm per query.")
    p.add_argument("--label", type=str, default="run", help="Label written into the output JSON.")
    p.add_argument("--output", type=str, default=None, help="Output JSON path (default stdout).")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.quick:
        cold_iters, warm_iters = 1, 1
    else:
        cold_iters, warm_iters = args.cold_iters, args.warm_iters

    queries = (
        [q.strip() for q in args.queries.split(",") if q.strip()]
        if args.queries
        else list(_DEFAULT_QUERIES)
    )
    if not queries:
        print("no queries to run", file=sys.stderr)
        return 2

    print(
        f"perf_harness label={args.label!r} queries={len(queries)} "
        f"cold={cold_iters} warm={warm_iters}",
        file=sys.stderr,
    )

    summary = asyncio.run(_run(queries, cold_iters=cold_iters, warm_iters=warm_iters))
    summary["label"] = args.label
    summary["cache_attrs_cleared"] = list(_CACHE_ATTRS)

    blob = json.dumps(summary, indent=2)
    if args.output:
        Path(args.output).write_text(blob)
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(blob)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
