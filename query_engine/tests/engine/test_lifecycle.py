"""Regression tests for graph client lifecycle.

Covers:
- LiveGraphClient.close() exists and closes the underlying httpx client
- resolve_async closes a self-built client after the call completes
- resolve_async does not close an injected client
- Lifespan configures the qre logger (idempotent)
- Lifespan calls llm.warm() when no LLM is injected
- Boot fails when warm() raises LLMInfraError
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from qre.engine.app import create_app
from qre.engine.core import resolve, resolve_async
from qre.engine.errors import LLMInfraError
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


# resolve() sync-wrapper loop safety


class TestLifespanLogger:
    """Lifespan attaches a StreamHandler to the qre logger; the guard is idempotent."""

    def test_logger_handler_attached_on_startup(self):
        # Clear any handlers that earlier tests may have attached.
        qre_log = logging.getLogger("qre")
        qre_log.handlers.clear()

        app = create_app(graph=FakeGraph(), llm=FakeLLM())
        with TestClient(app):
            # Inside the lifespan the handler must be attached.
            assert any(
                isinstance(h, logging.StreamHandler) for h in qre_log.handlers
            )

    def test_logger_handler_idempotent(self):
        # Running the lifespan twice must not double-add handlers.
        qre_log = logging.getLogger("qre")
        qre_log.handlers.clear()

        app = create_app(graph=FakeGraph(), llm=FakeLLM())
        # First lifespan run
        with TestClient(app):
            pass
        count_after_first = len(qre_log.handlers)

        app2 = create_app(graph=FakeGraph(), llm=FakeLLM())
        # Second lifespan run — should not add another handler.
        with TestClient(app2):
            assert len(qre_log.handlers) == count_after_first


class TestLifespanWarm:
    """Lifespan calls llm.warm() when no LLM is injected; re-raises on failure."""

    def test_warm_called_when_no_llm_injected(self):
        app = create_app(graph=FakeGraph())  # llm=None triggers warm
        with patch("qre.engine.llm.warm") as mock_warm:
            with TestClient(app):
                mock_warm.assert_called_once()

    def test_warm_not_called_when_llm_injected(self):
        app = create_app(graph=FakeGraph(), llm=FakeLLM())
        with patch("qre.engine.llm.warm") as mock_warm:
            with TestClient(app):
                mock_warm.assert_not_called()

    def test_boot_fails_on_missing_gemini_key(self):
        app = create_app(graph=FakeGraph())  # llm=None triggers warm
        with patch(
            "qre.engine.llm.warm",
            side_effect=LLMInfraError("GEMINI_API_KEY is required"),
        ):
            with pytest.raises(LLMInfraError):
                with TestClient(app, raise_server_exceptions=True):
                    pass  # Startup should raise before reaching here


class TestResolveLoopSafe:
    """The sync resolve() must work both standalone and inside a running loop.

    The Langfuse experiment runner awaits the task inside its own event loop, so a
    bare asyncio.run() in resolve() raises "cannot be called from a running event
    loop". These tests pin both call sites with a stubbed resolve_async.
    """

    def _patch_async(self, monkeypatch, sentinel):
        async def fake_resolve_async(request):
            return sentinel
        monkeypatch.setattr("qre.engine.core.resolve_async", fake_resolve_async)

    def test_standalone_call(self, monkeypatch):
        sentinel = object()
        self._patch_async(monkeypatch, sentinel)
        req = ResolveRequest(input=RawTextInput(query="x"))
        assert resolve(req) is sentinel

    def test_call_from_running_loop(self, monkeypatch):
        sentinel = object()
        self._patch_async(monkeypatch, sentinel)
        req = ResolveRequest(input=RawTextInput(query="x"))

        async def driver():
            # Mimics Langfuse calling the sync task from inside its loop.
            return resolve(req)

        assert asyncio.run(driver()) is sentinel
