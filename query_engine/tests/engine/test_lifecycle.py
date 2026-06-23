"""Regression tests for graph client lifecycle.

Covers:
- LiveGraphClient.close() exists and closes the underlying httpx client
- resolve_async closes a self-built client after the call completes
- resolve_async does not close an injected client
"""
from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import patch

from qre.engine.core import resolve_async
from qre.engine.graph import LiveGraphClient
from qre.models import RawTextInput, ResolveRequest
from tests.fixtures import FakeGraph, FakeLLM

PINNED_DATE = date(2026, 6, 23)
_FAKE_LLM = FakeLLM()


# LiveGraphClient.close()


class TestLiveGraphClientClose:
    def test_close_method_exists(self):
        assert hasattr(LiveGraphClient, "close")
        assert callable(LiveGraphClient.close)

    def test_close_closes_httpx_client(self):
        client = LiveGraphClient(base="http://localhost:9999")
        httpx_client = client._client
        assert not httpx_client.is_closed
        client.close()
        assert httpx_client.is_closed

    def test_close_is_idempotent(self):
        client = LiveGraphClient(base="http://localhost:9999")
        client.close()
        client.close()


# resolve_async lifecycle


class _TrackingGraph(FakeGraph):
    """FakeGraph that records close() calls."""

    def __init__(self):
        super().__init__()
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


def _run(query: str, graph=None):
    req = ResolveRequest(input=RawTextInput(query=query))
    with patch("qre.engine.extract.date") as mock_date:
        mock_date.today.return_value = PINNED_DATE
        mock_date.side_effect = lambda *a, **kw: date(*a, **kw)
        return asyncio.run(resolve_async(req, graph=graph, llm=_FAKE_LLM))


class TestResolveAsyncLifecycle:
    def test_injected_graph_is_not_closed(self):
        tracking = _TrackingGraph()
        _run("health ODA grants from USA to Ethiopia", graph=tracking)
        assert tracking.close_count == 0

    def test_self_built_graph_is_closed(self):
        closed_instances: list[bool] = []

        class _SpyClient(FakeGraph):
            def __init__(self_inner):
                super().__init__()

            def close(self_inner):
                closed_instances.append(True)

        with patch("qre.engine.core.LiveGraphClient", return_value=_SpyClient()):
            _run("health ODA grants from USA to Ethiopia", graph=None)

        assert len(closed_instances) == 1
