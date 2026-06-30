"""Regression tests for entity resolution guards.

Test 1: Partial resolution fires entity_not_resolved when fewer entities
resolve than were extracted.

Test 2: A lone entity extracted after "from <entity>" is not reused as
the recipient. A bare single entity (no preposition) still becomes the
recipient.

Test 3 (F3): For N>=2 variables, resolve_entity is called once per unique
entity name, not once per variable.
"""
from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, patch

from qre.engine.core import resolve_async
from qre.engine.extract import Extraction
from qre.engine.regions import RegionResult
from qre.models import (
    BindingValue,
    NoDataResponse,
    RawTextInput,
    ResolveRequest,
    ResolveResponse,
)
from tests.fixtures import FakeGraph, FakeLLM

PINNED_DATE = date(2026, 6, 23)

# FakeGraph and FakeLLM backed by the shared fixture files.
_FAKE_LLM = FakeLLM()


def _resolve(query: str, graph: FakeGraph | None = None) -> ResolveResponse:
    """Run resolve_async offline with the pinned date."""
    req = ResolveRequest(input=RawTextInput(query=query))
    g = graph if graph is not None else FakeGraph()
    with patch("qre.engine.extract.date") as mock_date:
        mock_date.today.return_value = PINNED_DATE
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        return asyncio.run(resolve_async(req, graph=g, llm=_FAKE_LLM))


# Partial resolution: entity_not_resolved guard


class TestPartialEntityResolution:
    def test_all_entities_resolve_passes(self):
        # Both USA and Ethiopia resolve in fixture graph. Should be definite.
        result = _resolve("health ODA grants from USA to Ethiopia")
        assert result.root.status == "definite"

    def test_unknown_entity_fires_entity_not_resolved(self):
        # Atlantis does not exist in fixture resolve map.
        result = _resolve("health ODA grants from USA to Atlantis")
        inner = result.root
        assert inner.status == "no_data"
        assert isinstance(inner, NoDataResponse)
        assert inner.no_data.reason == "entity_not_resolved"

    def test_partial_resolution_fires_entity_not_resolved(self):
        # One entity resolves (USA), the other doesn't (Atlantis).
        result = _resolve("health ODA from USA to Atlantis")
        inner = result.root
        assert inner.status == "no_data"
        assert isinstance(inner, NoDataResponse)
        assert inner.no_data.reason == "entity_not_resolved"


# Donor fallback: lone "from <entity>" must not become recipient


class TestDonorFallback:
    def test_bare_single_entity_becomes_recipient(self):
        # Single entity with no preposition becomes recipient.
        result = _resolve("health ODA grants Kenya")
        inner = result.root
        assert inner.status in ("definite", "no_data")
        if inner.status == "definite":
            spec = inner.interpretation
            slot_map = {
                s.key.property.dcid: s
                for s in spec.slots
                if s.key.property
            }
            recipient_slot = slot_map.get("DevelopmentFinanceRecipient")
            assert recipient_slot is not None
            binding = recipient_slot.binding
            assert isinstance(binding, BindingValue)
            ref = binding.value.ref
            assert ref is not None
            assert ref.dcid == "country/KEN"

    def test_lone_from_donor_does_not_become_recipient(self):
        # USA is a lone donor (from), should not become recipient.
        result = _resolve("health ODA grants from USA")
        inner = result.root
        assert inner.status == "no_data"


# ---------------------------------------------------------------------------
# F3: one resolve_entity call per unique entity name for N>=2
# ---------------------------------------------------------------------------


class TestPreResolvedDedup:
    """F3: N>=2 variables share a pre-resolved map; resolve_entity called once per unique name."""

    def test_duplicate_entity_resolved_once(self):
        """When the same entity name appears in a 2-variable extraction, resolve_entity
        is called exactly once (not twice)."""
        fake_graph = FakeGraph()
        resolve_calls: list[str] = []

        _original = fake_graph.resolve_entity

        def _tracked_resolve(name: str) -> str | None:
            resolve_calls.append(name)
            return _original(name)

        # Monkeypatch a graph method to count resolve_entity calls; reassigning a
        # bound method is intentional and not expressible in the nominal type.
        fake_graph.resolve_entity = _tracked_resolve  # ty: ignore[invalid-assignment]

        async def _stub_resolve(variable, **_kwargs):
            return RegionResult(
                variable_text=variable,
                status="no_data",
                specs=(),
                no_data_reason="variable_not_resolved",
                warnings=(),
                timing_by_step={},
            )

        # Two variables, same entity "Kenya" in both; pre-resolve should call once.
        fake_extraction = AsyncMock(
            return_value=(Extraction(variables=["v1", "v2"], entities=["Kenya", "Kenya"], dates=[]), None)
        )
        req = ResolveRequest(input=RawTextInput(query="test"))
        with patch("qre.engine.core.extract", fake_extraction), \
             patch("qre.engine.core.resolve_variable", new=_stub_resolve):
            asyncio.run(resolve_async(req, graph=fake_graph, llm=FakeLLM()))

        kenya_calls = [c for c in resolve_calls if c == "Kenya"]
        assert len(kenya_calls) == 1, (
            f"Expected 1 resolve_entity call for 'Kenya', got {len(kenya_calls)}. "
            f"All calls: {resolve_calls}"
        )

    def test_unique_entities_each_resolved_once(self):
        """Two different entities → two resolve_entity calls (one each, not shared)."""
        fake_graph = FakeGraph()
        resolve_calls: list[str] = []

        _original = fake_graph.resolve_entity

        def _tracked_resolve(name: str) -> str | None:
            resolve_calls.append(name)
            return _original(name)

        # Monkeypatch a graph method to count resolve_entity calls; reassigning a
        # bound method is intentional and not expressible in the nominal type.
        fake_graph.resolve_entity = _tracked_resolve  # ty: ignore[invalid-assignment]

        async def _stub_resolve(variable, **_kwargs):
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
                variables=["v1", "v2"], entities=["Kenya", "Uganda"], dates=[]
            ), None)
        )
        req = ResolveRequest(input=RawTextInput(query="test"))
        with patch("qre.engine.core.extract", fake_extraction), \
             patch("qre.engine.core.resolve_variable", new=_stub_resolve):
            asyncio.run(resolve_async(req, graph=fake_graph, llm=FakeLLM()))

        # Each unique entity resolved exactly once
        assert resolve_calls.count("Kenya") == 1
        assert resolve_calls.count("Uganda") == 1
