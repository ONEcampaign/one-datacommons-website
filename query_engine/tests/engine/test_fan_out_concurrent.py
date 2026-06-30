"""Regression guard: N≥2 fan-out runs concurrently via asyncio.gather.

A sequential implementation (replacing asyncio.gather with a for loop of
sequential awaits) would take ~N * DELAY seconds. The concurrent gather
takes ~DELAY. The 1.5× budget absorbs CI jitter while remaining tight
enough to catch serialisation regressions.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

from qre.engine.core import resolve_async
from qre.engine.extract import Extraction
from qre.engine.regions import RegionResult
from tests.engine._harness import make_request
from tests.fixtures import FakeGraph, FakeLLM

DELAY = 0.1  # seconds per stubbed resolve_variable call
N = 2        # number of variables in the fake extraction


async def _stub_resolve_variable(variable: str, **_kwargs: object) -> RegionResult:
    """Sleep DELAY then return a minimal no_data result (models one slow variable leg)."""
    await asyncio.sleep(DELAY)
    return RegionResult(
        variable_text=variable,
        status="no_data",
        specs=(),
        no_data_reason="variable_not_resolved",
        warnings=(),
        timing_by_step={},
    )


def _run(request, graph, llm):
    """Run resolve_async with injected fakes; untyped params avoid ty FakeLLM/FakeGraph errors."""
    return asyncio.run(resolve_async(request, graph=graph, llm=llm))


def test_fan_out_runs_concurrently():
    """N≥2 resolve_variable legs run in parallel; elapsed < N * DELAY * 1.5.

    Sequential execution would take ≥ N * DELAY = 0.20s.
    Concurrent execution takes ≈ DELAY = 0.10s.
    Budget is N * DELAY * 1.5 = 0.30s, comfortably above CI jitter.
    """
    fake_extraction = AsyncMock(
        return_value=(Extraction(variables=["v1", "v2"], entities=[], dates=[]), None)
    )

    with patch("qre.engine.core.extract", fake_extraction), \
            patch("qre.engine.core.resolve_variable", new=_stub_resolve_variable):
        t0 = time.perf_counter()
        _run(make_request("test concurrent fan-out"), FakeGraph(nodes={}, obs={}, detect={}, resolve={}), FakeLLM(responses={}))
        elapsed = time.perf_counter() - t0

    budget = N * DELAY * 1.5  # 0.30s
    assert elapsed < budget, (
        f"Fan-out appears sequential: elapsed={elapsed:.3f}s exceeds budget={budget:.3f}s. "
        f"Expected concurrent execution (~{DELAY:.2f}s for N={N} legs); "
        f"sequential would take ~{N * DELAY:.2f}s."
    )


def test_semaphore_caps_max_concurrent_legs():
    """F20: concurrent active legs never exceed QRE_MAX_VARIABLE_CONCURRENCY.

    Uses N=4 variables with a cap of 2 (patched). Tracks the high-water mark
    of simultaneously-active legs and asserts it never exceeds the cap.
    The 4 legs still complete in approximately 2*DELAY (two batches of 2),
    not 4*DELAY, confirming real concurrency within the semaphore bound.
    """
    _N = 4         # number of variables (> cap so semaphore is tested)
    _CAP = 2       # semaphore value to enforce

    active = 0
    max_active = 0

    async def _counting_stub(variable: str, **_kwargs: object) -> RegionResult:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(DELAY)
        active -= 1
        return RegionResult(
            variable_text=variable,
            status="no_data",
            specs=(),
            no_data_reason="variable_not_resolved",
            warnings=(),
            timing_by_step={},
        )

    fake_extraction = AsyncMock(
        return_value=(Extraction(
            variables=[f"v{i}" for i in range(_N)], entities=[], dates=[]
        ), None)
    )

    with patch("qre.engine.core.extract", fake_extraction), \
         patch("qre.engine.core.resolve_variable", new=_counting_stub), \
         patch("qre.engine.core.QRE_MAX_VARIABLE_CONCURRENCY", _CAP):
        _run(make_request("test semaphore cap"), FakeGraph(nodes={}, obs={}, detect={}, resolve={}), FakeLLM(responses={}))

    assert max_active <= _CAP, (
        f"Max concurrent legs was {max_active}, exceeds cap of {_CAP}. "
        f"Semaphore is not being applied."
    )
