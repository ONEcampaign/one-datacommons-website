"""Offline test harness: resolve_async with FakeLLM + FakeGraph.

The date is pinned to 2026-06-23 for fixture key stability across calendar days,
since the extract system prompt embeds [[TODAY]].
"""
from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import patch

from qre.engine.core import resolve_async
from qre.models import (
    BindingValue,
    BreadthDim,
    Coverage,
    CoverageBreadth,
    CoverageExact,
    GraphRef,
    RawTextInput,
    ResolveOptions,
    ResolveRequest,
    ResolveResponse,
    Slot,
)
from tests.fixtures import FakeGraph, FakeLLM

PINNED_DATE = date(2026, 6, 23)

_FAKE_GRAPH = FakeGraph()
_FAKE_LLM = FakeLLM()


def ref_dcid(ref: GraphRef | None) -> str:
    """Return the dcid of a graph ref the caller requires to be present.

    SlotValue.ref is Optional (literal/time-window values carry no ref); these
    tests only inspect value-bound slots, where the ref is always grounded.
    The assert makes that precondition explicit instead of an AttributeError.
    """
    assert ref is not None
    return ref.dcid


def slot_value_dcid(slot: Slot) -> str:
    """Return the grounded dcid of a single value-bound slot.

    Asserts the binding is a BindingValue carrying a graph ref, which is the
    precondition every call site already relies on.
    """
    binding = slot.binding
    assert isinstance(binding, BindingValue)
    return ref_dcid(binding.value.ref)


def dimensions_of(coverage: Coverage) -> list[BreadthDim]:
    """Return a coverage's breadth dimensions, asserting they are present.

    CoverageBare carries no dimensions and CoverageExact.dimensions is Optional;
    these tests only inspect coverages that populate dimensions, so the asserts
    encode that precondition instead of leaking the union/Optional to call sites.
    """
    assert isinstance(coverage, (CoverageExact, CoverageBreadth))
    assert coverage.dimensions is not None
    return coverage.dimensions


def make_request(query: str, pac: bool | None = None) -> ResolveRequest:
    """Build a ResolveRequest from a raw-text query and optional place_as_constraint."""
    options = ResolveOptions(place_as_constraint=pac) if pac is not None else None
    return ResolveRequest(input=RawTextInput(query=query), options=options)


def offline_resolve(request: ResolveRequest) -> ResolveResponse:
    """Run resolve_async with FakeLLM + FakeGraph, date pinned to 2026-06-23.

    The date pin keeps extraction fixture keys stable across calendar days.
    The patch targets qre.engine.extract.date so only the system prompt
    construction is affected.
    """
    with patch("qre.engine.extract.date") as mock_date:
        mock_date.today.return_value = PINNED_DATE
        # Allow date(*args) to still construct real date objects
        mock_date.side_effect = lambda *args, **kwargs: date(*args, **kwargs)
        return asyncio.run(
            resolve_async(request, graph=_FAKE_GRAPH, llm=_FAKE_LLM)
        )
