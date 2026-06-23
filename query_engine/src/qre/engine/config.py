"""Engine configuration: read from environment variables with documented defaults.

All config is read at import time so missing env vars surface immediately on startup.
No call into LLM or graph is made here.
"""
import os

# ---------------------------------------------------------------------------
# LLM config
# ---------------------------------------------------------------------------

# Model used for all LLM calls.
QRE_ENGINE_MODEL: str = os.getenv("QRE_ENGINE_MODEL", "gemini-flash-lite-latest")

# ---------------------------------------------------------------------------
# Graph config
# ---------------------------------------------------------------------------

# Dev-finance is staging-only (zero on prod).
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

# Maximum query length accepted at the FastAPI layer; rejected with 422 before
# any LLM or graph call is made.
QRE_MAX_QUERY_CHARS: int = int(os.getenv("QRE_MAX_QUERY_CHARS", "2000"))

# ---------------------------------------------------------------------------
# Engine build identity
# ---------------------------------------------------------------------------

ENGINE_BUILD_ID: str = os.getenv("QRE_ENGINE_BUILD", "qre-engine-dev")
