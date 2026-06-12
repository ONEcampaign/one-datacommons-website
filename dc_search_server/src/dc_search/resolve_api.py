"""Resolvekit demo — backend API router.

Exposes eight POST endpoints under /api/resolve-demo/api/:
  /resolve, /explain, /suggest, /parse, /graph, /bulk, /compare, /byod

All endpoints return HTTP 200 with a ``status`` discriminator (either the
operation result or ``{status:"error", detail, elapsed_ms}``).  ``/parse``
returns 200 ``{status:"unavailable", detail}`` when the ``[parsing]`` extra
is missing.  ``/bulk`` returns the server-picked column in the response so the
frontend can render the column-picker on the first round-trip.

The module is written so that ``from dc_search import resolve_api`` NEVER
raises even when ``resolvekit`` or its competitors are absent.  A broken
import yields ``router = None``; ``app.py`` checks for that before calling
``app.include_router``.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import threading
import time
from collections import OrderedDict
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guarded imports — never raise at module level
# ---------------------------------------------------------------------------

_RESOLVEKIT_OK = False
_PARSE_OK = False

try:
    from resolvekit import Resolver as _Resolver

    _RESOLVEKIT_OK = True
except Exception:
    logger.warning("resolvekit not importable — resolve endpoints will be unavailable")

try:
    import resolvekit as _rk

    _PARSE_OK = hasattr(_rk, "parse")
except Exception:
    pass

_CC_OK = False
try:
    import country_converter as _coco  # noqa: F401 — imported for side-effects/warm

    _CC_OK = True
except Exception:
    logger.warning("country_converter not importable — /compare will skip coco")

_HDX_OK = False
try:
    from pathlib import Path as _Path

    import hdx.location  # noqa: F401 — imported to trigger package init
    from hdx.location.country import Country as _Country

    # hdx-python-country's script_dir_plus_file() uses hdx/utilities/path.py's
    # __file__ to build _ochapath, landing it in hdx/utilities/ — but the CSV
    # lives in hdx/location/.  Patch the class attribute to the correct path
    # before the first call so offline mode works out of the box.
    _csv_name = "Countries & Territories Taxonomy MVP - C&T Taxonomy.csv"
    _correct_csv = _Path(hdx.location.__file__).parent / _csv_name
    if _correct_csv.exists():
        _Country._ochapath = _correct_csv  # type: ignore[attr-defined]
        _Country._ochapath_default = _correct_csv  # type: ignore[attr-defined]
    _Country.set_use_live_default(use_live=False)
    _HDX_OK = True
except Exception:
    logger.warning("hdx-python-country not importable — /compare will skip hdx")

# ---------------------------------------------------------------------------
# Process-singleton Resolver (double-checked lock, fail-safe)
# ---------------------------------------------------------------------------

_RESOLVER: Any = None
_RESOLVER_SENTINEL = object()  # stored on build failure so we don't retry
_RESOLVER_LOCK = threading.Lock()
_RESOLVE_TIMEOUT: float = 10.0  # seconds per call

_CC_INSTANCE: Any = None  # warmed CountryConverter singleton


def _get_resolver() -> Any:
    """Return the process-singleton Resolver, building it on first call.

    Returns the Resolver on success.  Returns ``_RESOLVER_SENTINEL`` when
    ``resolvekit`` failed to import or the build itself raised.  Never raises.
    """
    global _RESOLVER
    if _RESOLVER is not None:
        return _RESOLVER
    with _RESOLVER_LOCK:
        if _RESOLVER is not None:
            return _RESOLVER
        if not _RESOLVEKIT_OK:
            _RESOLVER = _RESOLVER_SENTINEL
            return _RESOLVER_SENTINEL
        try:
            module_ids = _demo_module_ids()
            logger.info("resolvekit: building Resolver with modules %s", module_ids)
            _RESOLVER = _Resolver.from_modules(  # type: ignore[name-defined]
                module_ids=module_ids,
                cache_size=0,
                warm=True,
            )
        except Exception:
            logger.exception("Failed to build resolvekit Resolver — resolve endpoints degraded")
            _RESOLVER = _RESOLVER_SENTINEL
    return _RESOLVER


def _demo_module_ids() -> list[str]:
    """The resolvekit modules the demo loads — full breadth, listed explicitly.

    Lists every locally-available module (bundled geo + org packs, plus the remote
    ``geo.cities`` and ``geo.admin1``–``admin5`` packs when present). An explicit
    list keeps resolution deterministic across machines, unlike ``Resolver.auto()``
    which silently varies with whatever data-packs happen to be cached.

    ``RESOLVE_DEMO_DOWNLOAD=1`` (default) best-effort fetches the remote packs
    (~800 MB: cities + admin) on first build so a fresh deploy gets the full set;
    set it to ``0`` to load only what is already on disk.
    """
    if os.getenv("RESOLVE_DEMO_DOWNLOAD", "1") == "1":
        try:
            _rk.download_all()  # idempotent — skips packs already cached
        except Exception:
            logger.warning(
                "resolvekit download_all failed — loading available packs only",
                exc_info=True,
            )
    available = [m.module_id for m in _rk.modules() if getattr(m, "is_available", False)]
    # Fall back to the bundled lite-geo set if introspection somehow finds nothing.
    return sorted(available) or list(_Resolver._LITE_GEO_MODULE_IDS)  # type: ignore[attr-defined]


def _get_cc() -> Any:
    """Return a warmed CountryConverter singleton, or None."""
    global _CC_INSTANCE
    if _CC_INSTANCE is not None:
        return _CC_INSTANCE
    if not _CC_OK:
        return None
    try:
        _CC_INSTANCE = _coco.CountryConverter()  # type: ignore[name-defined]
    except Exception:
        logger.exception("Failed to build CountryConverter")
    return _CC_INSTANCE


# Warm both singletons at module import (best-effort; errors are swallowed).
if _RESOLVEKIT_OK:
    try:
        _get_resolver()
    except Exception:
        pass

if _CC_OK:
    try:
        _get_cc()
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Static error messages — NEVER put str(e)/repr(e) in a response
# ---------------------------------------------------------------------------

_ERR_RESOLVER_UNAVAILABLE = "Resolver unavailable."
_ERR_RESOLVE_TIMEOUT = "Resolution timed out."
_ERR_RESOLVE_FAILED = "Resolution failed."
_ERR_SUGGEST_FAILED = "Suggestion failed."
_ERR_PARSE_UNAVAILABLE = "Parse capability not installed ([parsing] extra required)."
_ERR_PARSE_FAILED = "Parsing failed."
_ERR_GRAPH_FAILED = "Graph query failed."
_ERR_BULK_FAILED = "Bulk resolution failed."
_ERR_COMPARE_FAILED = "Comparison failed."
_ERR_BAD_CSV = "Could not parse CSV/TSV input."
_ERR_COLUMN_NOT_FOUND = "Specified column not found in headers."
_ERR_BYOD_NO_NAME = "Your records need a 'name' column."
_ERR_BYOD_BUILD = "Could not build a resolver from those records."


def _error_envelope(detail: str, elapsed_ms: float) -> dict[str, Any]:
    return {"status": "error", "detail": detail, "elapsed_ms": elapsed_ms}


# ---------------------------------------------------------------------------
# Pydantic request models
# ---------------------------------------------------------------------------

class ResolveReq(BaseModel):
    query: str = Field(min_length=1, max_length=200)


class ExplainReq(BaseModel):
    query: str = Field(min_length=1, max_length=200)


# Typeahead scope — maps to resolvekit suggest() filters. "all" applies no
# filter (countries, cities, admin units, orgs — everything), so a city like
# "Paris" or a group like "NATO" surfaces; the narrower scopes restrict it.
SuggestScope = Literal["all", "countries", "cities", "orgs"]

_SCOPE_KWARGS: dict[str, dict[str, Any]] = {
    "all": {},
    "countries": {"entity_type": "geo.country"},
    "cities": {"entity_type": "geo.city"},
    "orgs": {"domain": "org"},
}


class SuggestReq(BaseModel):
    prefix: str = Field(default="", max_length=100)
    top_k: int = Field(default=10, ge=1, le=25)
    scope: SuggestScope = "all"


class ParseReq(BaseModel):
    text: str = Field(default="", max_length=4000)


class GraphReq(BaseModel):
    group: str = Field(min_length=1, max_length=100)
    region: str = Field(min_length=1, max_length=100)
    as_of_year: int = Field(ge=1900, le=2100)


class BulkReq(BaseModel):
    csv_text: str = Field(default="", max_length=200_000)
    column: str = Field(default="", max_length=100)


CompareTarget = Literal["name", "iso2", "iso3", "dcid", "wikidata"]


class CompareReq(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    to: CompareTarget = "iso3"


class ByodReq(BaseModel):
    """Bring-your-own-data: a small CSV of custom records + a query to resolve
    against a resolver built from them via ``Resolver.from_records``."""

    records_csv: str = Field(default="", max_length=20_000)
    query: str = Field(default="", max_length=200)


# Per-dataset resolver cache for /byod — keyed by the records CSV text so
# repeated queries on the same dataset reuse the (re)built resolver instead of
# rebuilding on every keystroke. Small LRU-ish cap; demo-scale only.
_BYOD_RESOLVERS: "OrderedDict[str, tuple[Any, int]]" = OrderedDict()
_BYOD_CACHE_CAP = 8
_BYOD_LOCK = threading.Lock()
_BYOD_MAX_ROWS = 2000

_BYOD_NAME_HEADERS = ("name", "entity", "label", "title")
_BYOD_ID_HEADERS = ("id", "entity_id", "record_id")
_BYOD_ALIAS_HEADERS = ("aliases", "alias", "akas", "also_known_as")
_BYOD_CODE_HEADERS = ("code", "codes", "symbol", "ticker", "abbr")


def _byod_find_col(headers: list[str], candidates: tuple[str, ...]) -> str | None:
    low = {h.lower().strip(): h for h in headers}
    for c in candidates:
        if c in low:
            return low[c]
    return None


def _get_byod_resolver(csv_text: str, headers: list[str], rows: list[list[str]]) -> tuple[Any, int]:
    """Build (or fetch from cache) a standalone resolver over user records.

    Returns ``(resolver, record_count)``. Raises ``ValueError`` when there is no
    usable name column.
    """
    key = csv_text
    with _BYOD_LOCK:
        cached = _BYOD_RESOLVERS.get(key)
        if cached is not None:
            _BYOD_RESOLVERS.move_to_end(key)
            return cached

    name_col = _byod_find_col(headers, _BYOD_NAME_HEADERS)
    if name_col is None:
        raise ValueError(_ERR_BYOD_NO_NAME)
    id_col = _byod_find_col(headers, _BYOD_ID_HEADERS)
    alias_col = _byod_find_col(headers, _BYOD_ALIAS_HEADERS)
    code_col = _byod_find_col(headers, _BYOD_CODE_HEADERS)

    records: list[dict[str, str]] = []
    for row in rows[:_BYOD_MAX_ROWS]:
        if not row:
            continue
        rec = {headers[i]: row[i] for i in range(min(len(headers), len(row)))}
        if (rec.get(name_col) or "").strip():
            records.append(rec)

    kwargs: dict[str, Any] = {"name": name_col, "warm": True}
    if id_col:
        kwargs["id"] = id_col
    if alias_col:
        kwargs["aliases"] = alias_col
    if code_col:
        kwargs["codes"] = [code_col]

    resolver = _Resolver.from_records(records, **kwargs)  # type: ignore[name-defined]

    with _BYOD_LOCK:
        _BYOD_RESOLVERS[key] = (resolver, len(records))
        _BYOD_RESOLVERS.move_to_end(key)
        while len(_BYOD_RESOLVERS) > _BYOD_CACHE_CAP:
            _BYOD_RESOLVERS.popitem(last=False)
    return resolver, len(records)


# Which output targets each comparison tool can emit. resolvekit handles all of
# them; the others are narrower — the gaps are the point of the comparison.
_COCO_TARGET = {"name": "name_short", "iso2": "ISO2", "iso3": "ISO3"}  # dcid/wikidata: unsupported
_HDX_TARGETS = {"iso3"}  # hdx only emits ISO3


def _rk_target_value(entity: Any, target: str) -> str | None:
    """Render a resolvekit entity in the requested target, or None if it has none
    (e.g. a city has no ISO3)."""
    if entity is None:
        return None
    if target == "name":
        return getattr(entity, "canonical_name", None)
    if target in ("iso2", "iso3"):
        return getattr(entity, target, None)
    codes = getattr(entity, "codes_dict", None) or {}
    if target == "dcid":
        return codes.get("dcid") or getattr(entity, "entity_id", None)
    if target == "wikidata":
        return codes.get("wikidata")
    return None


# ---------------------------------------------------------------------------
# Helper: highlight slicing for /suggest
# ---------------------------------------------------------------------------

_DISPLAY_CAP_MEMBERS = 28
_DISPLAY_CAP_BULK = 30
_BULK_DISTINCT_CAP = 500


def _split_highlight(
    canonical_name: str, highlight_ranges: list[tuple[int, int]]
) -> tuple[str, str, str]:
    """Slice *canonical_name* into (pre, hl, post) using the first highlight range.

    Returns ``(canonical_name, "", "")`` for fuzzy matches (empty ranges).
    """
    if not highlight_ranges:
        return canonical_name, "", ""
    a, b = highlight_ranges[0]
    return canonical_name[:a], canonical_name[a:b], canonical_name[b:]


# ---------------------------------------------------------------------------
# Helper: id_color for resolution status
# ---------------------------------------------------------------------------

_STATUS_COLORS: dict[str, str] = {
    "resolved": "#108479",   # teal — matches ONE Data RESOLVED token
    "ambiguous": "#F5A623",  # amber
    "no_match": "#D0021B",   # red
    "error": "#9B9B9B",      # grey
}


def _id_color(status: str) -> str:
    return _STATUS_COLORS.get(status, "#9B9B9B")


# Sentinel dicts for /bulk — defined here so _STATUS_COLORS is already resolved.
_FALLBACK_NO_MATCH: dict[str, Any] = {
    "entity_id": None,
    "id_color": _STATUS_COLORS["no_match"],
    "iso3": None,
    "conf": "—",
    "status": "no_match",
}
_NOT_PROCESSED: dict[str, Any] = {
    "entity_id": None,
    "id_color": _STATUS_COLORS["error"],
    "iso3": None,
    "conf": "—",
    "status": "not_processed",
}


# ---------------------------------------------------------------------------
# Helper: conf display string
# ---------------------------------------------------------------------------


def _conf_str(confidence: float | None, status: str) -> str:
    if status == "ambiguous":
        return "ambig"
    if confidence is None:
        return "—"
    return f"{confidence * 100:.1f}%"


def _bar_w(confidence: float | None) -> str:
    if confidence is None:
        return "0%"
    return f"{round(confidence * 100)}%"


# ---------------------------------------------------------------------------
# Helper: parse pivots from entity
# ---------------------------------------------------------------------------


def _make_pivots(entity: Any) -> list[dict[str, str]]:
    if entity is None:
        return []
    pivots: list[dict[str, str]] = []
    codes = entity.codes_dict or {}
    for key, label in [
        ("iso2", "ISO 2"),
        ("iso3", "ISO 3"),
        ("dcid", "DC ID"),
        ("wikidata", "Wikidata"),
        ("iso_numeric", "ISO numeric"),
    ]:
        val = codes.get(key)
        if val:
            pivots.append({"k": label, "v": val})
    # canonical name as first pivot
    if entity.canonical_name:
        pivots.insert(0, {"k": "Name", "v": entity.canonical_name})
    return pivots


# ---------------------------------------------------------------------------
# Helper: CSV/TSV parse
# ---------------------------------------------------------------------------

_PLACE_LIKE_HEADERS = frozenset(
    {"country", "nation", "place", "location", "territory", "region", "area", "land", "geo"}
)


def _parse_csv(csv_text: str) -> tuple[list[str], list[list[str]]]:
    """Parse CSV or TSV, sniffing the delimiter.

    Returns ``(headers, rows)`` where each row is a list of raw cell strings.
    Raises ``ValueError`` on parse failure.
    """
    # Strip leading BOM before any other processing.
    text = csv_text.lstrip("﻿").strip()
    if not text:
        return [], []
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel  # type: ignore[assignment]
    reader = csv.reader(io.StringIO(text), dialect)
    rows = list(reader)
    if not rows:
        return [], []
    headers = rows[0]
    return headers, rows[1:]


def _pick_column(headers: list[str]) -> str:
    """Pick the most place-like column header, falling back to the first."""
    if not headers:
        return ""
    for h in headers:
        if h.strip().lower() in _PLACE_LIKE_HEADERS:
            return h
    return headers[0]


# ---------------------------------------------------------------------------
# Router — only built if resolvekit imported successfully
# ---------------------------------------------------------------------------

if not _RESOLVEKIT_OK:
    router: APIRouter | None = None
else:
    router = APIRouter(prefix="/api/resolve-demo/api")

    # -----------------------------------------------------------------------
    # POST /resolve
    # -----------------------------------------------------------------------

    @router.post("/resolve")
    async def resolve_endpoint(req: ResolveReq) -> dict[str, Any]:
        t0 = time.perf_counter()

        q = req.query.strip().replace("\x00", "")
        if not q:
            return _error_envelope("Query must not be blank.", (time.perf_counter() - t0) * 1000)

        # Bare digit strings (e.g. "42") resolve via exotic numeric code systems
        # and produce nonsensical results. Skip resolution for digit-only input.
        if q.isdigit():
            return {
                "status": "no_match",
                "entity_id": None,
                "canonical_name": None,
                "iso2": None,
                "iso3": None,
                "confidence": None,
                "conf_pct": "—",
                "bar_w": "0%",
                "match_tier": None,
                "pivots": [],
                "candidates": [],
                "reason": "digit_only",
                "reason_note": (
                    "Bare digit strings are not resolved"
                    " (numeric code disambiguation disabled)."
                ),
                "explain_text": None,
                "elapsed_ms": (time.perf_counter() - t0) * 1000,
            }

        r = _get_resolver()
        if r is _RESOLVER_SENTINEL:
            return _error_envelope(_ERR_RESOLVER_UNAVAILABLE, 0.0)

        try:
            res = await asyncio.wait_for(
                asyncio.to_thread(r.resolve, q, include_entity=True),
                timeout=_RESOLVE_TIMEOUT,
            )
        except TimeoutError:
            logger.exception("resolve timed out for query=%r", q)
            return _error_envelope(_ERR_RESOLVE_TIMEOUT, (time.perf_counter() - t0) * 1000)
        except Exception:
            logger.exception("resolve failed for query=%r", q)
            return _error_envelope(_ERR_RESOLVE_FAILED, (time.perf_counter() - t0) * 1000)

        elapsed_ms = (time.perf_counter() - t0) * 1000
        status_val = res.status.value

        if status_val == "resolved":
            ent = res.entity
            conf = res.confidence
            return {
                "status": "resolved",
                "entity_id": res.entity_id,
                "canonical_name": ent.canonical_name if ent else None,
                "iso2": ent.iso2 if ent else None,
                "iso3": ent.iso3 if ent else None,
                "confidence": conf,
                "conf_pct": _conf_str(conf, "resolved"),
                "bar_w": _bar_w(conf),
                "match_tier": res.match_tier.value if res.match_tier else None,
                "pivots": _make_pivots(ent),
                "candidates": [],
                "reason": None,
                "reason_note": None,
                "explain_text": None,
                "elapsed_ms": elapsed_ms,
            }

        if status_val == "ambiguous":
            candidates = [
                {
                    "entity_id": c.entity_id,
                    "name": getattr(c, "canonical_name", None) or c.entity_id,
                    "confidence": c.confidence,
                    "conf_pct": _conf_str(c.confidence, "resolved"),
                    "bar_w": _bar_w(c.confidence),
                }
                for c in (res.candidates or [])[:3]
            ]
            return {
                "status": "ambiguous",
                "entity_id": None,
                "canonical_name": None,
                "iso2": None,
                "iso3": None,
                "confidence": None,
                "conf_pct": "ambig",
                "bar_w": "0%",
                "match_tier": None,
                "pivots": [],
                "candidates": candidates,
                "reason": None,
                "reason_note": "Multiple matches found; please be more specific.",
                "explain_text": None,
                "elapsed_ms": elapsed_ms,
            }

        # no_match (and error fallback)
        reason_code = res.reasons[0].value if res.reasons else "unknown"
        reason_notes: dict[str, str] = {
            "sentinel_blocked": "Input is a known sentinel value (n/a, null, etc.).",
            "no_candidates": "No candidates found for this query.",
            "ambiguous_low_gap": "Candidates were too similar to pick one.",
        }
        return {
            "status": "no_match",
            "entity_id": None,
            "canonical_name": None,
            "iso2": None,
            "iso3": None,
            "confidence": None,
            "conf_pct": "—",
            "bar_w": "0%",
            "match_tier": None,
            "pivots": [],
            "candidates": [],
            "reason": reason_code,
            "reason_note": reason_notes.get(reason_code, "No match found."),
            "explain_text": None,
            "elapsed_ms": elapsed_ms,
        }

    # -----------------------------------------------------------------------
    # POST /explain
    # -----------------------------------------------------------------------

    @router.post("/explain")
    async def explain_endpoint(req: ExplainReq) -> dict[str, Any]:
        """Return a full resolution scorecard for *query*.

        Runs the pipeline a second time (~98ms); the frontend calls this only
        when the user clicks the "explain" toggle on the resolved entity it is
        displaying — not on every keystroke.
        """
        t0 = time.perf_counter()

        q = req.query.strip().replace("\x00", "")
        if not q:
            return _error_envelope("Query must not be blank.", (time.perf_counter() - t0) * 1000)

        r = _get_resolver()
        if r is _RESOLVER_SENTINEL:
            return _error_envelope(_ERR_RESOLVER_UNAVAILABLE, 0.0)

        try:

            def _run_explain(query: str) -> str:
                res = r.resolve(query, include_entity=True)
                txt = res.explain(verbosity="full").as_text()
                return txt

            explain_text = await asyncio.wait_for(
                asyncio.to_thread(_run_explain, q),
                timeout=_RESOLVE_TIMEOUT,
            )
        except TimeoutError:
            logger.exception("explain timed out for query=%r", q)
            return _error_envelope(_ERR_RESOLVE_TIMEOUT, (time.perf_counter() - t0) * 1000)
        except Exception:
            logger.exception("explain failed for query=%r", q)
            return _error_envelope(_ERR_RESOLVE_FAILED, (time.perf_counter() - t0) * 1000)

        return {
            "status": "ok",
            "explain_text": explain_text,
            "elapsed_ms": (time.perf_counter() - t0) * 1000,
        }

    # -----------------------------------------------------------------------
    # POST /suggest
    # -----------------------------------------------------------------------

    @router.post("/suggest")
    async def suggest_endpoint(req: SuggestReq) -> dict[str, Any]:
        t0 = time.perf_counter()
        r = _get_resolver()
        if r is _RESOLVER_SENTINEL:
            return _error_envelope(_ERR_RESOLVER_UNAVAILABLE, 0.0)

        scope_kwargs = _SCOPE_KWARGS.get(req.scope, {})
        try:
            suggestions = await asyncio.wait_for(
                asyncio.to_thread(
                    r.suggest, req.prefix, top_k=req.top_k, **scope_kwargs
                ),
                timeout=_RESOLVE_TIMEOUT,
            )
        except TimeoutError:
            logger.exception("suggest timed out for prefix=%r", req.prefix)
            return _error_envelope(_ERR_RESOLVE_TIMEOUT, (time.perf_counter() - t0) * 1000)
        except Exception:
            logger.exception("suggest failed for prefix=%r", req.prefix)
            return _error_envelope(_ERR_SUGGEST_FAILED, (time.perf_counter() - t0) * 1000)

        results = []
        for s in suggestions:
            pre, hl, post = _split_highlight(s.canonical_name, s.highlight_ranges or [])
            results.append(
                {
                    "entity_id": s.entity_id,
                    "canonical_name": s.canonical_name,
                    "match_class": s.match_class.value,
                    "pre": pre,
                    "hl": hl,
                    "post": post,
                }
            )

        prefix_str = req.prefix.strip()
        header = f'suggest("{prefix_str}") → {len(results)}' if prefix_str else "suggest(…)"
        return {
            "status": "ok",
            "results": results,
            "header": header,
            "elapsed_ms": (time.perf_counter() - t0) * 1000,
        }

    # -----------------------------------------------------------------------
    # POST /parse
    # -----------------------------------------------------------------------

    @router.post("/parse")
    async def parse_endpoint(req: ParseReq) -> dict[str, Any]:
        t0 = time.perf_counter()

        if not _PARSE_OK:
            return {
                "status": "unavailable",
                "detail": _ERR_PARSE_UNAVAILABLE,
                "elapsed_ms": 0.0,
            }

        r = _get_resolver()
        if r is _RESOLVER_SENTINEL:
            return _error_envelope(_ERR_RESOLVER_UNAVAILABLE, 0.0)

        try:

            def _run_parse(text: str) -> Any:
                return _rk.parse(text)  # type: ignore[name-defined]

            parse_result = await asyncio.wait_for(
                asyncio.to_thread(_run_parse, req.text),
                timeout=_RESOLVE_TIMEOUT,
            )
        except TimeoutError:
            logger.exception("parse timed out")
            return _error_envelope(_ERR_RESOLVE_TIMEOUT, (time.perf_counter() - t0) * 1000)
        except Exception:
            logger.exception("parse failed")
            return _error_envelope(_ERR_PARSE_FAILED, (time.perf_counter() - t0) * 1000)

        # Build highlight segments over the full input text.
        text = req.text
        segments: list[dict[str, Any]] = []
        entities: list[dict[str, Any]] = []
        spans = list(parse_result)

        # Walk char by char building non-overlapping segments.
        pos = 0
        for span in spans:
            if span.start > pos:
                # plain text between spans
                segments.append(
                    {
                        "text": text[pos : span.start],
                        "bg": None,
                        "underline": False,
                        "weight": "normal",
                    }
                )
            resolved = span.entity_id is not None
            seg_status = "resolved" if resolved else "unlinked"
            conf = span.confidence
            segments.append(
                {
                    "text": text[span.start : span.end],
                    "bg": "#CFF3EC" if resolved else "#FFF3CD",
                    "underline": resolved,
                    "weight": "bold" if resolved else "normal",
                }
            )
            entities.append(
                {
                    "surface": span.surface,
                    "range": [span.start, span.end],
                    "id": span.entity_id,
                    "id_color": _id_color(seg_status),
                    "conf_pct": _conf_str(conf, seg_status) if conf is not None else None,
                    "status": seg_status,
                }
            )
            pos = span.end

        if pos < len(text):
            segments.append(
                {"text": text[pos:], "bg": None, "underline": False, "weight": "normal"}
            )

        return {
            "status": "ok",
            "segments": segments,
            "entities": entities,
            "count": len(entities),
            "elapsed_ms": (time.perf_counter() - t0) * 1000,
        }

    # -----------------------------------------------------------------------
    # POST /graph
    # -----------------------------------------------------------------------

    @router.post("/graph")
    async def graph_endpoint(req: GraphReq) -> dict[str, Any]:
        t0 = time.perf_counter()
        r = _get_resolver()
        if r is _RESOLVER_SENTINEL:
            return _error_envelope(_ERR_RESOLVER_UNAVAILABLE, 0.0)

        try:

            def _run_graph(group: str, region: str, as_of_year: int) -> dict[str, Any]:
                as_of = date(as_of_year, 1, 1)

                # Resolve group by name to get the canonical entity_id.
                group_res = r.resolve(group, include_entity=True)
                group_name = (
                    group_res.entity.canonical_name
                    if group_res.entity
                    else group
                )
                group_entity_id = group_res.entity_id or group

                members_iso3 = r.members_of(group, as_of=as_of, as_codes="iso3")
                total_members = len(members_iso3)
                displayed = members_iso3[:_DISPLAY_CAP_MEMBERS]
                more_count = total_members - len(displayed)

                region_res = r.resolve(region, include_entity=True)
                region_name = (
                    region_res.entity.canonical_name
                    if region_res.entity
                    else region
                )
                within_note = "Countries within this geographic region (geo.country edges)."
                try:
                    raw_within = r.within(region, entity_type="geo.country", to="iso3")
                except Exception:
                    raw_within = []
                # Filter out None values that can appear for unlinked entries.
                within_codes = [c for c in raw_within if c is not None]
                if not within_codes:
                    # Not a geographic container (e.g. a membership-defined set
                    # like a World Bank income group): within() yields nothing (or
                    # raises). Fall back to members_of so the panel lists its
                    # countries.
                    try:
                        members = r.members_of(region, as_of=as_of, as_codes="iso3")
                    except Exception:
                        members = []
                    members = [c for c in members if c is not None]
                    if members:
                        within_codes = members
                        within_note = "Member countries of this group (membership edges)."

                subject_in_group = r.is_member("United Kingdom", group, as_of=as_of)

                return {
                    "group_entity_id": group_entity_id,
                    "group_name": group_name,
                    "members_iso3": members_iso3,
                    "displayed": displayed,
                    "more_count": more_count,
                    "total_members": total_members,
                    "region_name": region_name,
                    "within_codes": within_codes,
                    "within_note": within_note,
                    "subject_in_group": subject_in_group,
                }

            data = await asyncio.wait_for(
                asyncio.to_thread(_run_graph, req.group, req.region, req.as_of_year),
                timeout=_RESOLVE_TIMEOUT,
            )
        except TimeoutError:
            logger.exception("graph timed out")
            return _error_envelope(_ERR_RESOLVE_TIMEOUT, (time.perf_counter() - t0) * 1000)
        except Exception:
            logger.exception("graph failed")
            return _error_envelope(_ERR_GRAPH_FAILED, (time.perf_counter() - t0) * 1000)

        elapsed_ms = (time.perf_counter() - t0) * 1000
        more_count = data["more_count"]
        return {
            "status": "ok",
            "members": {
                "name": data["group_name"],
                "entity_id": data["group_entity_id"],
                "count": data["total_members"],
                "as_of_label": f"as of {req.as_of_year}",
                "members": data["displayed"],
                "more": more_count > 0,
                "more_label": f"+{more_count} more" if more_count > 0 else "",
            },
            "within": {
                "name": data["region_name"],
                "count": len(data["within_codes"]),
                "codes": data["within_codes"],
                "note": data["within_note"],
            },
            "subject_in_group": data["subject_in_group"],
            "elapsed_ms": elapsed_ms,
        }

    # -----------------------------------------------------------------------
    # POST /bulk
    # -----------------------------------------------------------------------

    @router.post("/bulk")
    async def bulk_endpoint(req: BulkReq) -> dict[str, Any]:
        t0 = time.perf_counter()

        # Parse the CSV/TSV first (sync, cheap).
        try:
            headers, rows = _parse_csv(req.csv_text)
        except Exception:
            logger.exception("bulk CSV parse failed")
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return _error_envelope(_ERR_BAD_CSV, elapsed_ms)

        if not headers:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return {
                "status": "ok",
                "results": [],
                "summary": {"rows": 0, "unique": 0, "resolved": 0},
                "headers": [],
                "column": "",
                "more": False,
                "more_label": "",
                "elapsed_ms": elapsed_ms,
            }

        # Resolve column selection.
        column = req.column.strip()
        if not column:
            column = _pick_column(headers)
        elif column not in headers:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return _error_envelope(_ERR_COLUMN_NOT_FOUND, elapsed_ms)

        col_idx = headers.index(column)
        raw_values = [row[col_idx] for row in rows if len(row) > col_idx]
        total_rows = len(raw_values)

        # Dedup (preserving order); track the full distinct set separately from
        # the processing cap so summary.unique is always truthful.
        seen: dict[str, None] = {}
        for v in raw_values:
            seen.setdefault(v, None)
        all_distinct = list(seen.keys())
        # Cap the resolved set; values beyond the cap are marked not_processed
        # rather than no_match so the summary is honest.
        distinct = all_distinct[:_BULK_DISTINCT_CAP]
        over_cap_set = set(all_distinct[_BULK_DISTINCT_CAP:])
        truncated = bool(over_cap_set)

        r = _get_resolver()
        if r is _RESOLVER_SENTINEL:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return _error_envelope(_ERR_RESOLVER_UNAVAILABLE, elapsed_ms)

        try:

            def _run_bulk(values: list[str]) -> Any:
                return _rk.bulk(  # type: ignore[name-defined]
                    values=values,
                    to="iso3",
                    on_ambiguous="null",
                    not_found="null",
                    on_missing="null",
                    output="record",
                )

            bulk_result = await asyncio.wait_for(
                asyncio.to_thread(_run_bulk, distinct),
                timeout=_RESOLVE_TIMEOUT,
            )
        except TimeoutError:
            logger.exception("bulk timed out")
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return _error_envelope(_ERR_RESOLVE_TIMEOUT, elapsed_ms)
        except Exception:
            logger.exception("bulk failed")
            elapsed_ms = (time.perf_counter() - t0) * 1000
            return _error_envelope(_ERR_BULK_FAILED, elapsed_ms)

        # Build a per-distinct-value lookup from BulkResult.values.
        value_map: dict[str, dict[str, Any]] = {}
        resolved_count = 0
        for item_data in bulk_result.values:
            query_text = item_data.get("query_text", "")
            status_str = item_data.get("status", "no_match")
            entity_id = item_data.get("entity_id")
            confidence = item_data.get("confidence")
            iso3 = item_data.get("value")
            if status_str == "resolved":
                resolved_count += 1
            value_map[query_text] = {
                "entity_id": entity_id,
                "id_color": _id_color(status_str),
                "iso3": iso3,
                "conf": _conf_str(confidence, status_str),
                "status": status_str,
            }

        # Map each original row value through the lookup.
        result_rows: list[dict[str, Any]] = []
        for v in raw_values:
            if v in over_cap_set:
                info = _NOT_PROCESSED
            else:
                info = value_map.get(v, _FALLBACK_NO_MATCH)
            result_rows.append({
                "input": v,
                "id": info["entity_id"],
                "id_color": info["id_color"],
                "iso3": info["iso3"],
                "conf": info["conf"],
            })

        displayed = result_rows[:_DISPLAY_CAP_BULK]
        more_count = len(result_rows) - len(displayed)

        return {
            "status": "ok",
            "results": displayed,
            "summary": {
                "rows": total_rows,
                "unique": len(all_distinct),
                "resolved": resolved_count,
            },
            "truncated": truncated,
            "headers": headers,
            "column": column,
            "more": more_count > 0,
            "more_label": f"+{more_count} more rows" if more_count > 0 else "",
            "elapsed_ms": (time.perf_counter() - t0) * 1000,
        }

    # -----------------------------------------------------------------------
    # POST /compare
    # -----------------------------------------------------------------------

    @router.post("/compare")
    async def compare_endpoint(req: CompareReq) -> dict[str, Any]:
        t0 = time.perf_counter()
        q = req.query
        to = req.to  # output target: name | iso2 | iso3 | dcid | wikidata

        r = _get_resolver()
        if r is _RESOLVER_SENTINEL:
            return _error_envelope(_ERR_RESOLVER_UNAVAILABLE, 0.0)

        try:

            async def _time_resolvekit() -> dict[str, Any]:
                t = time.perf_counter()

                def _run() -> Any:
                    return r.resolve(q, include_entity=True)

                res = await asyncio.wait_for(
                    asyncio.to_thread(_run), timeout=_RESOLVE_TIMEOUT
                )
                elapsed = (time.perf_counter() - t) * 1000
                conf = res.confidence
                status_val = res.status.value
                # resolvekit can emit every target; value may still be None for a
                # given entity (e.g. a city has no ISO3).
                value = _rk_target_value(getattr(res, "entity", None), to)

                # When the resolver ABSTAINED on an ambiguous query (no single
                # answer, but candidates exist), surface the top few rendered in
                # the requested target — so the cell shows *why* it abstained
                # rather than a bare "no match". Gated on a non-resolved status so
                # a resolved entity that merely lacks the target code (e.g. NATO
                # has no ISO3) still reports as resolved, not as candidates.
                # Candidate summaries don't carry codes, so look up each full
                # entity by id; this runs after the timer, so it never inflates
                # the reported latency.
                candidates: list[dict[str, Any]] = []
                if value is None and status_val != "resolved" and getattr(res, "candidates", None):
                    def _enrich() -> list[dict[str, Any]]:
                        out: list[dict[str, Any]] = []
                        for c in res.candidates[:3]:
                            try:
                                ent = r.entity(c.entity_id)
                            except Exception:
                                ent = None
                            out.append(
                                {
                                    "value": _rk_target_value(ent, to),
                                    "name": getattr(c, "canonical_name", None) or c.entity_id,
                                    "conf_pct": _conf_str(c.confidence, "resolved"),
                                }
                            )
                        return out

                    try:
                        candidates = await asyncio.wait_for(
                            asyncio.to_thread(_enrich), timeout=_RESOLVE_TIMEOUT
                        )
                    except Exception:
                        logger.warning("compare candidate enrichment failed", exc_info=True)

                return {
                    "supported": True,
                    "value": value,
                    "candidates": candidates,
                    "status": status_val,
                    "elapsed_ms": elapsed,
                    "offline": True,
                    "deterministic": True,
                    "confidence": True,
                    "scope": "country, admin, city, org",
                    "entity_id": res.entity_id,
                    "conf_pct": _conf_str(conf, status_val),
                }

            async def _time_coco() -> dict[str, Any]:
                t = time.perf_counter()
                cc = _get_cc()
                coco_to = _COCO_TARGET.get(to)
                if cc is None or coco_to is None:
                    return {
                        "supported": coco_to is not None,
                        "value": None,
                        "elapsed_ms": (time.perf_counter() - t) * 1000,
                        "offline": True,
                        "deterministic": True,
                        "confidence": False,
                        "scope": "country",
                        "note": "unavailable" if cc is None else "emits names/codes only",
                    }

                def _run() -> str:
                    val = cc.convert(names=q, to=coco_to)
                    return str(val) if val and val != "not found" else "not found"

                result = await asyncio.wait_for(
                    asyncio.to_thread(_run), timeout=_RESOLVE_TIMEOUT
                )
                elapsed = (time.perf_counter() - t) * 1000
                return {
                    "supported": True,
                    "value": None if result == "not found" else result,
                    "elapsed_ms": elapsed,
                    "offline": True,
                    "deterministic": True,
                    "confidence": False,
                    "scope": "country",
                }

            async def _time_hdx() -> dict[str, Any]:
                t = time.perf_counter()
                if to not in _HDX_TARGETS:
                    return {
                        "supported": False,
                        "value": None,
                        "elapsed_ms": (time.perf_counter() - t) * 1000,
                        "offline": True,
                        "deterministic": True,
                        "confidence": False,
                        "scope": "country",
                        "note": "emits ISO3 only",
                    }
                if not _HDX_OK:
                    return {
                        "supported": True,
                        "value": None,
                        "elapsed_ms": 0.0,
                        "offline": True,
                        "deterministic": True,
                        "confidence": False,
                        "scope": "country",
                        "note": "unavailable",
                    }

                def _run() -> tuple[str | None, bool]:
                    _Country.set_use_live_default(use_live=False)  # type: ignore[name-defined]
                    iso3, is_exact = _Country.get_iso3_country_code_fuzzy(q)  # type: ignore[name-defined]
                    return iso3, is_exact

                iso3, is_exact = await asyncio.wait_for(
                    asyncio.to_thread(_run), timeout=_RESOLVE_TIMEOUT
                )
                elapsed = (time.perf_counter() - t) * 1000
                return {
                    "supported": True,
                    "value": iso3,
                    "elapsed_ms": elapsed,
                    "offline": True,
                    "deterministic": True,
                    "confidence": False,
                    "scope": "country",
                    "is_exact": is_exact,
                }

            rk_data, cc_data, hdx_data = await asyncio.gather(
                _time_resolvekit(),
                _time_coco(),
                _time_hdx(),
                return_exceptions=False,
            )

        except TimeoutError:
            logger.exception("compare timed out for query=%r", q)
            return _error_envelope(_ERR_RESOLVE_TIMEOUT, (time.perf_counter() - t0) * 1000)
        except Exception:
            logger.exception("compare failed for query=%r", q)
            return _error_envelope(_ERR_COMPARE_FAILED, (time.perf_counter() - t0) * 1000)

        return {
            "status": "ok",
            "to": to,
            "resolvekit": rk_data,
            "country_converter": cc_data,
            "hdx_python_country": hdx_data,
            "elapsed_ms": (time.perf_counter() - t0) * 1000,
        }

    # -----------------------------------------------------------------------
    # POST /byod — resolve a query against a user-supplied record set
    # -----------------------------------------------------------------------

    @router.post("/byod")
    async def byod_endpoint(req: ByodReq) -> dict[str, Any]:
        """Build a standalone resolver from the caller's own records (via
        ``Resolver.from_records``) and resolve *query* against it.

        Demonstrates that resolvekit's matching (codes, aliases, fuzzy, calibrated
        confidence) works on data it has never seen — not just the bundled packs.
        """
        t0 = time.perf_counter()

        try:
            headers, rows = _parse_csv(req.records_csv)
        except Exception:
            logger.exception("byod CSV parse failed")
            return _error_envelope(_ERR_BAD_CSV, (time.perf_counter() - t0) * 1000)

        if not headers:
            return {
                "status": "ok",
                "record_count": 0,
                "resolution": None,
                "elapsed_ms": (time.perf_counter() - t0) * 1000,
            }

        try:
            resolver, record_count = await asyncio.to_thread(
                _get_byod_resolver, req.records_csv, headers, rows
            )
        except ValueError:
            return _error_envelope(_ERR_BYOD_NO_NAME, (time.perf_counter() - t0) * 1000)
        except Exception:
            logger.exception("byod resolver build failed")
            return _error_envelope(_ERR_BYOD_BUILD, (time.perf_counter() - t0) * 1000)

        q = req.query.strip().replace("\x00", "")
        if not q:
            return {
                "status": "ok",
                "record_count": record_count,
                "resolution": None,
                "elapsed_ms": (time.perf_counter() - t0) * 1000,
            }

        try:
            res = await asyncio.wait_for(
                asyncio.to_thread(resolver.resolve, q, include_entity=True),
                timeout=_RESOLVE_TIMEOUT,
            )
        except TimeoutError:
            logger.exception("byod resolve timed out")
            return _error_envelope(_ERR_RESOLVE_TIMEOUT, (time.perf_counter() - t0) * 1000)
        except Exception:
            logger.exception("byod resolve failed")
            return _error_envelope(_ERR_RESOLVE_FAILED, (time.perf_counter() - t0) * 1000)

        status_val = res.status.value
        ent = getattr(res, "entity", None)
        conf = res.confidence
        resolution = {
            "status": status_val,
            "entity_id": res.entity_id,
            "canonical_name": ent.canonical_name if ent else None,
            "confidence": conf,
            "conf_pct": _conf_str(conf, status_val),
            "bar_w": _bar_w(conf),
            "match_tier": res.match_tier.value if res.match_tier else None,
        }
        return {
            "status": "ok",
            "record_count": record_count,
            "resolution": resolution,
            "elapsed_ms": (time.perf_counter() - t0) * 1000,
        }
