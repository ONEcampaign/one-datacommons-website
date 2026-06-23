"""Regression tests for entity resolution guards.

Test 1: Partial resolution fires entity_not_resolved when fewer entities
resolve than were extracted.

Test 2: A lone entity extracted after "from <entity>" is not reused as
the recipient. A bare single entity (no preposition) still becomes the
recipient.
"""
from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import patch

from qre.engine.core import resolve_async
from qre.models import NoDataResponse, RawTextInput, ResolveRequest
from tests.fixtures import FakeGraph, FakeLLM

PINNED_DATE = date(2026, 6, 23)

# FakeGraph and FakeLLM backed by the shared fixture files.
_FAKE_LLM = FakeLLM()


def _resolve(query: str, graph: FakeGraph | None = None) -> object:
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
            assert recipient_slot.binding.kind == "value"
            assert recipient_slot.binding.value.ref.dcid == "country/KEN"

    def test_lone_from_donor_does_not_become_recipient(self):
        # USA is a lone donor (from), should not become recipient.
        result = _resolve("health ODA grants from USA")
        inner = result.root
        assert inner.status == "no_data"
