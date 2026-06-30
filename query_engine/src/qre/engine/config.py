"""Engine configuration: read from environment variables with documented defaults.

All config is read at import time so missing env vars surface immediately on startup.
No call into LLM or graph is made here.
"""
import os
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# LLM config
# ---------------------------------------------------------------------------

# Model used for all LLM calls.
QRE_ENGINE_MODEL: str = os.getenv("QRE_ENGINE_MODEL", "gemini-flash-lite-latest")

# ---------------------------------------------------------------------------
# Graph config
# ---------------------------------------------------------------------------

QRE_GRAPH_BASE: str = os.getenv("QRE_GRAPH_BASE", "https://dc-staging.one.org")

# Confirmed working UA string — Cloudflare blocks non-browser user agents.
BROWSER_UA: str = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------

# Graph HTTP read timeout in seconds.
QRE_GRAPH_TIMEOUT_S: float = float(os.getenv("QRE_GRAPH_TIMEOUT_S", "30"))

# Overall request deadline in seconds; asyncio.wait_for wraps resolve_async.
# Prevents sequential graph rounds from stacking unbounded.
QRE_REQUEST_TIMEOUT_S: float = float(os.getenv("QRE_REQUEST_TIMEOUT_S", "45"))

# ---------------------------------------------------------------------------
# Seam
# ---------------------------------------------------------------------------

# Server default for place-as-constraint. ON means recipients bind directionally.
# Overridable per-request via ResolveOptions.place_as_constraint.
_seam_raw = os.getenv("QRE_SEAM_DEFAULT", "true").lower()
QRE_SEAM_DEFAULT: bool = _seam_raw in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# Security / prompt safety
# ---------------------------------------------------------------------------

# CORS allowlist for the /api/qre/* endpoints. Comma-separated origins;
# empty default adds no CORS headers (no wildcard). Read at create_app()
# time via parse_cors_origins() so tests can patch the env var after import.
def parse_cors_origins() -> list[str]:
    """Return CORS allowlist from QRE_CORS_ORIGINS; empty list when absent."""
    return [o.strip() for o in os.getenv("QRE_CORS_ORIGINS", "").split(",") if o.strip()]

# Maximum query length accepted at the FastAPI layer; rejected with 422 before
# any LLM or graph call is made.
QRE_MAX_QUERY_CHARS: int = int(os.getenv("QRE_MAX_QUERY_CHARS", "2000"))

# ---------------------------------------------------------------------------
# Shape discovery
# ---------------------------------------------------------------------------

# Maximum number of recalled candidate SVs that derive_shapes will confirm via node_arcs.
# The confirm fetch is one batched call so raising this pool widens recall without adding
# round-trips. Resolvers consume the carried arc facts.
QRE_MAX_CONFIRM_CANDIDATES: int = int(os.getenv("QRE_MAX_CONFIRM_CANDIDATES", "40"))

# Minimum cosine similarity for a recalled SV to be treated as a match.
# Suppresses low-confidence noise while keeping clear matches.
# Gates variable_not_resolved for genuinely unknown variables.
QRE_RELEVANCE_THRESHOLD: float = float(os.getenv("QRE_RELEVANCE_THRESHOLD", "0.5"))

# Representative cosine above QRE_RELEVANCE_THRESHOLD but below this fires
# RETRIEVAL_SCORE_WEAK (info); 0.65 marks a weak but non-trivial match.
QRE_WEAK_SCORE_THRESHOLD: float = float(os.getenv("QRE_WEAK_SCORE_THRESHOLD", "0.65"))

# Maximum number of candidate specs returned in a CandidatesResponse.
# Clamped broadest-first when multiple five-tuple groups survive ranking.
QRE_MAX_CANDIDATES: int = int(os.getenv("QRE_MAX_CANDIDATES", "6"))

# Maximum number of variables resolved per query. Variables beyond this cap are
# silently dropped and surfaced via a VARIABLES_CLAMPED warning.
QRE_MAX_VARIABLES: int = int(os.getenv("QRE_MAX_VARIABLES", "6"))

# Minimum cosine-score margin between the top standard shape's representative SV and the
# second's for the engine to resolve definite rather than candidates.
QRE_DOMINANCE_MARGIN: float = float(os.getenv("QRE_DOMINANCE_MARGIN", "0.15"))

# ---------------------------------------------------------------------------
# Variable pipeline concurrency
# ---------------------------------------------------------------------------

# Maximum number of variable legs to run concurrently within a single request.
# Must be >= QRE_MAX_VARIABLES so the semaphore never silently caps below the
# variable ceiling.
QRE_MAX_VARIABLE_CONCURRENCY: int = int(os.getenv("QRE_MAX_VARIABLE_CONCURRENCY", "8"))

# ---------------------------------------------------------------------------
# Client-side caches (per-LiveGraphClient, cleared on restart)
# ---------------------------------------------------------------------------

# Maximum entries in the observation facets LRU cache keyed by
# (stat_var, entity, needs_dates). Per-process; cleared on restart.
QRE_OBS_CACHE_SIZE: int = int(os.getenv("QRE_OBS_CACHE_SIZE", "512"))

# Maximum entries in the detect_svs LRU cache keyed by
# (query, QRE_RELEVANCE_THRESHOLD). Per-process; a config change restarts the
# process and naturally invalidates.
QRE_DETECT_CACHE_SIZE: int = int(os.getenv("QRE_DETECT_CACHE_SIZE", "256"))

# ---------------------------------------------------------------------------
# Engine build identity
# ---------------------------------------------------------------------------

ENGINE_BUILD_ID: str = os.getenv("QRE_ENGINE_BUILD", "qre-engine-dev")

# ---------------------------------------------------------------------------
# Startup validation (raises ValueError at import, before the port binds)
# ---------------------------------------------------------------------------

if QRE_MAX_CONFIRM_CANDIDATES < 1:
    raise ValueError(
        f"QRE_MAX_CONFIRM_CANDIDATES must be >= 1; got {QRE_MAX_CONFIRM_CANDIDATES!r}"
    )
if not (0 < QRE_RELEVANCE_THRESHOLD <= 1):
    raise ValueError(
        f"QRE_RELEVANCE_THRESHOLD must be in (0, 1]; got {QRE_RELEVANCE_THRESHOLD!r}"
    )
if not (0 < QRE_WEAK_SCORE_THRESHOLD <= 1):
    raise ValueError(
        f"QRE_WEAK_SCORE_THRESHOLD must be in (0, 1]; got {QRE_WEAK_SCORE_THRESHOLD!r}"
    )
if QRE_MAX_CANDIDATES < 1:
    raise ValueError(
        f"QRE_MAX_CANDIDATES must be >= 1; got {QRE_MAX_CANDIDATES!r}"
    )
if QRE_MAX_VARIABLES < 1:
    raise ValueError(
        f"QRE_MAX_VARIABLES must be >= 1; got {QRE_MAX_VARIABLES!r}"
    )
if QRE_DOMINANCE_MARGIN < 0:
    raise ValueError(
        f"QRE_DOMINANCE_MARGIN must be >= 0; got {QRE_DOMINANCE_MARGIN!r}"
    )
if QRE_GRAPH_TIMEOUT_S <= 0:
    raise ValueError(
        f"QRE_GRAPH_TIMEOUT_S must be > 0; got {QRE_GRAPH_TIMEOUT_S!r}"
    )
if QRE_MAX_QUERY_CHARS < 1:
    raise ValueError(
        f"QRE_MAX_QUERY_CHARS must be >= 1; got {QRE_MAX_QUERY_CHARS!r}"
    )
if QRE_REQUEST_TIMEOUT_S <= 0:
    raise ValueError(
        f"QRE_REQUEST_TIMEOUT_S must be > 0; got {QRE_REQUEST_TIMEOUT_S!r}"
    )
if QRE_MAX_VARIABLE_CONCURRENCY < QRE_MAX_VARIABLES:
    raise ValueError(
        f"QRE_MAX_VARIABLE_CONCURRENCY ({QRE_MAX_VARIABLE_CONCURRENCY!r}) must be "
        f">= QRE_MAX_VARIABLES ({QRE_MAX_VARIABLES!r})"
    )
_graph_base_parsed = urlparse(QRE_GRAPH_BASE)
# https:// is allowed for any host. http:// is allowed ONLY for the loopback
# dev hosts, compared by exact hostname so spoofs like http://localhost.evil.com
# or http://127.0.0.1.attacker are rejected.
if not (
    _graph_base_parsed.scheme == "https"
    or (
        _graph_base_parsed.scheme == "http"
        and _graph_base_parsed.hostname in {"localhost", "127.0.0.1"}
    )
):
    raise ValueError(
        f"QRE_GRAPH_BASE must use https:// (or http://localhost / http://127.0.0.1 "
        f"for dev); got {QRE_GRAPH_BASE!r}"
    )
