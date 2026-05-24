"""Request-time telemetry: token usage and step timing.

Pricing tables are eval-only and live in the dc-search source repo.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Generator, Literal

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class Usage:
    """Compact, provider-agnostic token-usage record for one pipeline step."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cached_write_tokens: int = 0
    model_requests: int = 0
    model: str | None = None
    latency_s: float | None = None

    def add(self, other: Usage) -> Usage:
        """Return a new Usage that is the sum of self and other."""
        a = self.latency_s
        b = other.latency_s
        merged_latency = None if a is None and b is None else (a or 0.0) + (b or 0.0)
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            cached_write_tokens=self.cached_write_tokens + other.cached_write_tokens,
            model_requests=self.model_requests + other.model_requests,
            model=self.model or other.model,
            latency_s=merged_latency,
        )


class TelemetryLLMUsage(BaseModel):
    """Per-step LLM token usage record for the HTTP response telemetry block."""

    step: Literal["extract", "slot_bind"]
    input_tokens: int
    output_tokens: int
    model: str | None = None
    latency_s: float | None = None


@contextmanager
def step_timer(out: dict[str, Any]) -> Generator[None, None, None]:
    """Context manager that writes ``latency_s`` into ``out`` on exit.

    Usage::

        record: dict[str, Any] = {}
        with step_timer(record):
            do_work()
        print(record["latency_s"])
    """
    t0 = time.perf_counter()
    try:
        yield
    finally:
        out["latency_s"] = time.perf_counter() - t0
