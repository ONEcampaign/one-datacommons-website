"""Offline test harness: resolve_async with FakeLLM + FakeGraph.

The date is pinned to 2026-06-23 for fixture key stability across calendar days,
since the extract system prompt embeds [[TODAY]].
"""
from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import patch

from qre.engine.core import resolve_async
from qre.models import ResolveRequest, ResolveResponse
from tests.fixtures import FakeGraph, FakeLLM

PINNED_DATE = date(2026, 6, 23)

_FAKE_GRAPH = FakeGraph()
_FAKE_LLM = FakeLLM()


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
