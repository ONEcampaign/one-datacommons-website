"""Per-request degraded-call flag (ContextVar) and its two accessors.

Defined exactly once here and re-exported by retrieval/__init__.py so that
import paths remain stable for test fixtures and mocking.
"""

from __future__ import annotations

import contextvars

# Per-request flag set when a coverage/availability call fails open (returns
# empty result on transient error rather than raising). A ContextVar isolates
# it per asyncio task, so changes within a thread don't leak across requests.
_dc_call_degraded: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "_dc_call_degraded", default=False
)


def reset_dc_call_degraded() -> None:
    """Clear the per-request degraded flag (call before a batch of mixer calls)."""
    _dc_call_degraded.set(False)


def dc_call_was_degraded() -> bool:
    """True if a coverage/availability call has failed open since the last reset."""
    return _dc_call_degraded.get()
