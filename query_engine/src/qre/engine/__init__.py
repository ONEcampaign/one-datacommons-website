"""QRE engine subpackage.

Requires the 'engine' extra. Install with:
    uv sync --extra engine
    pip install 'qre[engine]'

Public surface: from qre.engine import resolve, resolve_async

This module is isolated: not imported by qre/__init__.py. The engine subpackage is
only pulled in when something explicitly imports qre.engine.
"""
# Probe import at import time to surface missing dependencies immediately.
try:
    import httpx as _httpx  # noqa: F401
except ImportError as _exc:
    raise ImportError(
        "qre.engine requires the 'engine' extra. "
        "Install with: uv sync --extra engine (or pip install 'qre[engine]')."
    ) from _exc

from qre.engine.core import resolve, resolve_async

__all__ = ["resolve", "resolve_async"]
