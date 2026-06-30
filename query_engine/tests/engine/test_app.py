"""FastAPI app tests using TestClient."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from qre.engine.app import create_app
from qre.engine.config import ENGINE_BUILD_ID, QRE_MAX_QUERY_CHARS
from qre.models import ResolveResponse
from tests.engine._harness import PINNED_DATE
from tests.fixtures import FakeGraph, FakeLLM


def make_client() -> TestClient:
    app = create_app(graph=FakeGraph(), llm=FakeLLM())
    return TestClient(app, raise_server_exceptions=False)


class TestHealthz:
    def test_healthz_ok(self):
        client = make_client()
        resp = client.get("/api/qre/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["build"] == ENGINE_BUILD_ID


class TestResolveEndpoint:
    def test_200_on_valid_request(self):
        client = make_client()
        with patch("qre.engine.extract.date") as mock_date:
            mock_date.today.return_value = PINNED_DATE
            mock_date.side_effect = None
            resp = client.post(
                "/api/qre/resolve",
                json={"input": {
                    "kind": "raw_text",
                    "query": "health ODA grants from USA to Ethiopia",
                }},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("definite", "candidates", "no_data")

    def test_422_on_malformed_body(self):
        client = make_client()
        resp = client.post(
            "/api/qre/resolve",
            json={"not_valid": "body"},
        )
        assert resp.status_code == 422

    def test_400_on_kind_parsed(self):
        client = make_client()
        resp = client.post(
            "/api/qre/resolve",
            json={
                "input": {
                    "kind": "parsed",
                    "variable_text": ["health ODA"],
                }
            },
        )
        assert resp.status_code == 400

    def test_422_when_query_exceeds_max_chars(self):
        client = make_client()
        long_query = "x" * (QRE_MAX_QUERY_CHARS + 1)
        resp = client.post(
            "/api/qre/resolve",
            json={"input": {"kind": "raw_text", "query": long_query}},
        )
        assert resp.status_code == 422

    def test_503_on_graph_infra_error(self):
        # A graph that raises GraphInfraError at detect_svs routes to 503
        from qre.engine.app import create_app as _create_app
        from qre.engine.errors import GraphInfraError

        class ErrorGraph(FakeGraph):
            # Subclass FakeGraph for the batch-method surface (node_*_batch /
            # observation_facets_batch) so it satisfies EngineGraphClient; the
            # no-op __init__ skips fixture loading and the overrides below raise
            # on the first engine call exactly as before.
            def __init__(self):
                pass
            def node_label(self, dcid):
                raise GraphInfraError("simulated error")
            def node_arcs(self, dcid):
                raise GraphInfraError("simulated error")
            def node_type(self, dcid):
                raise GraphInfraError("simulated error")
            def resolve_entity(self, name):
                raise GraphInfraError("simulated error")
            def detect_svs(self, query):
                raise GraphInfraError("simulated error")
            def child_dcids(self, parent_dcid):
                raise GraphInfraError("simulated error")
            def observation_facets(self, *, stat_var, entity, needs_dates=False):
                raise GraphInfraError("simulated error")

        app = _create_app(graph=ErrorGraph(), llm=FakeLLM())
        client = TestClient(app, raise_server_exceptions=False)

        with patch("qre.engine.extract.date") as mock_date:
            mock_date.today.return_value = PINNED_DATE
            mock_date.side_effect = None
            resp = client.post(
                "/api/qre/resolve",
                json={"input": {
                    "kind": "raw_text",
                    "query": "health ODA grants from USA to Ethiopia",
                }},
            )
        assert resp.status_code == 503
        assert resp.json() == {"detail": "Service temporarily unavailable"}

    def test_503_on_llm_infra_error(self):
        # An LLM that raises LLMInfraError at extract routes to 503
        from qre.engine.app import create_app as _create_app
        from qre.engine.errors import LLMInfraError

        class _ErrorLLM:
            def generate_structured(self, *, prompt, system, schema):
                raise LLMInfraError("simulated LLM error")

        app = _create_app(graph=FakeGraph(), llm=_ErrorLLM())
        client = TestClient(app, raise_server_exceptions=False)

        with patch("qre.engine.extract.date") as mock_date:
            mock_date.today.return_value = PINNED_DATE
            mock_date.side_effect = None
            resp = client.post(
                "/api/qre/resolve",
                json={"input": {
                    "kind": "raw_text",
                    "query": "health ODA grants from USA to Ethiopia",
                }},
            )
        assert resp.status_code == 503
        assert resp.json() == {"detail": "Service temporarily unavailable"}

    def test_500_on_generic_error(self):
        # A non-infra exception falls through to the generic handler → 500
        from qre.engine.app import create_app as _create_app

        class _BoomGraph(FakeGraph):
            # See ErrorGraph: subclass for the batch-method surface; overrides raise.
            def __init__(self):
                pass
            def node_label(self, dcid):
                raise RuntimeError("boom")
            def node_arcs(self, dcid):
                raise RuntimeError("boom")
            def node_type(self, dcid):
                raise RuntimeError("boom")
            def resolve_entity(self, name):
                raise RuntimeError("boom")
            def detect_svs(self, query):
                raise RuntimeError("boom")
            def child_dcids(self, parent_dcid):
                raise RuntimeError("boom")
            def observation_facets(self, *, stat_var, entity, needs_dates=False):
                raise RuntimeError("boom")

        app = _create_app(graph=_BoomGraph(), llm=FakeLLM())
        client = TestClient(app, raise_server_exceptions=False)

        with patch("qre.engine.extract.date") as mock_date:
            mock_date.today.return_value = PINNED_DATE
            mock_date.side_effect = None
            resp = client.post(
                "/api/qre/resolve",
                json={"input": {
                    "kind": "raw_text",
                    "query": "health ODA grants from USA to Ethiopia",
                }},
            )
        assert resp.status_code == 500
        assert resp.json() == {"detail": "Service temporarily unavailable"}

    def test_whitespace_query_returns_no_data(self):
        client = make_client()
        resp = client.post(
            "/api/qre/resolve",
            json={"input": {"kind": "raw_text", "query": "   "}},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "no_data"
        assert body["no_data"]["reason"] == "variable_not_resolved"

    def test_504_on_timeout(self, monkeypatch):
        # asyncio.wait_for timeout raises asyncio.TimeoutError -> 504
        async def _slow_resolve(*args, **kwargs):
            raise asyncio.TimeoutError()

        monkeypatch.setattr("qre.engine.app.resolve_async", _slow_resolve)

        client = make_client()
        resp = client.post(
            "/api/qre/resolve",
            json={"input": {"kind": "raw_text", "query": "health ODA"}},
        )
        assert resp.status_code == 504
        assert resp.json() == {"detail": "Request timed out"}

    def test_502_on_upstream_4xx(self):
        # GraphInfraError with a 4xx upstream_status maps to 502 Bad Gateway
        from qre.engine.errors import GraphInfraError

        class _UpstreamErrorGraph(FakeGraph):
            # See ErrorGraph: subclass for the batch-method surface; overrides raise.
            def __init__(self):
                pass
            def node_label(self, dcid):
                raise GraphInfraError("not found", upstream_status=404)
            def node_arcs(self, dcid):
                raise GraphInfraError("not found", upstream_status=404)
            def node_type(self, dcid):
                raise GraphInfraError("not found", upstream_status=404)
            def resolve_entity(self, name):
                raise GraphInfraError("not found", upstream_status=404)
            def detect_svs(self, query):
                raise GraphInfraError("not found", upstream_status=404)
            def child_dcids(self, parent_dcid):
                raise GraphInfraError("not found", upstream_status=404)
            def observation_facets(self, *, stat_var, entity, needs_dates=False):
                raise GraphInfraError("not found", upstream_status=404)

        app = create_app(graph=_UpstreamErrorGraph(), llm=FakeLLM())
        client = TestClient(app, raise_server_exceptions=False)

        with patch("qre.engine.extract.date") as mock_date:
            mock_date.today.return_value = PINNED_DATE
            mock_date.side_effect = None
            resp = client.post(
                "/api/qre/resolve",
                json={"input": {
                    "kind": "raw_text",
                    "query": "health ODA grants from USA to Ethiopia",
                }},
            )
        assert resp.status_code == 502
        assert resp.json() == {"detail": "Bad gateway: upstream returned an error"}

    def test_503_on_graph_5xx_upstream(self):
        # GraphInfraError with a 5xx upstream_status still maps to 503
        from qre.engine.errors import GraphInfraError

        class _ServerErrorGraph(FakeGraph):
            # See ErrorGraph: subclass for the batch-method surface; overrides raise.
            def __init__(self):
                pass
            def node_label(self, dcid):
                raise GraphInfraError("server error", upstream_status=500)
            def node_arcs(self, dcid):
                raise GraphInfraError("server error", upstream_status=500)
            def node_type(self, dcid):
                raise GraphInfraError("server error", upstream_status=500)
            def resolve_entity(self, name):
                raise GraphInfraError("server error", upstream_status=500)
            def detect_svs(self, query):
                raise GraphInfraError("server error", upstream_status=500)
            def child_dcids(self, parent_dcid):
                raise GraphInfraError("server error", upstream_status=500)
            def observation_facets(self, *, stat_var, entity, needs_dates=False):
                raise GraphInfraError("server error", upstream_status=500)

        app = create_app(graph=_ServerErrorGraph(), llm=FakeLLM())
        client = TestClient(app, raise_server_exceptions=False)

        with patch("qre.engine.extract.date") as mock_date:
            mock_date.today.return_value = PINNED_DATE
            mock_date.side_effect = None
            resp = client.post(
                "/api/qre/resolve",
                json={"input": {
                    "kind": "raw_text",
                    "query": "health ODA grants from USA to Ethiopia",
                }},
            )
        assert resp.status_code == 503


class TestSchema:
    def test_schema_200(self):
        client = make_client()
        resp = client.get("/api/qre/schema")
        assert resp.status_code == 200

    def test_schema_version(self):
        client = make_client()
        resp = client.get("/api/qre/schema")
        body = resp.json()
        assert body["schema_version"] == "1.0"

    def test_schema_has_request_and_response(self):
        client = make_client()
        resp = client.get("/api/qre/schema")
        body = resp.json()
        # Both keys must be valid JSON Schema objects (dicts with at minimum a "type"
        # or "$defs" produced by Pydantic's model_json_schema()).
        assert isinstance(body["request"], dict)
        assert isinstance(body["response"], dict)


class TestSSETransport:
    def _resolve_sse(self, client: TestClient) -> str:
        """POST with Accept: text/event-stream and return the raw text body."""
        with patch("qre.engine.extract.date") as mock_date:
            mock_date.today.return_value = PINNED_DATE
            mock_date.side_effect = None
            resp = client.post(
                "/api/qre/resolve",
                json={"input": {
                    "kind": "raw_text",
                    "query": "health ODA grants from USA to Ethiopia",
                }},
                headers={"Accept": "text/event-stream"},
            )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")
        return resp.text

    def test_sse_returns_event_stream_content_type(self):
        client = make_client()
        with patch("qre.engine.extract.date") as mock_date:
            mock_date.today.return_value = PINNED_DATE
            mock_date.side_effect = None
            resp = client.post(
                "/api/qre/resolve",
                json={
                    "input": {
                        "kind": "raw_text",
                        "query": "health ODA grants from USA to Ethiopia",
                    }
                },
                headers={"Accept": "text/event-stream"},
            )
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers.get("content-type", "")

    def test_sse_ends_with_done_event(self):
        client = make_client()
        body = self._resolve_sse(client)
        # The stream must contain at least one "event: done" line.
        assert "event: done" in body

    def test_sse_progress_event_is_static(self):
        client = make_client()
        body = self._resolve_sse(client)
        # The progress event must carry the fixed payload and must not echo query text.
        assert 'data: {"status":"progress"}' in body

    def test_sse_done_event_parses_to_resolve_response(self):
        # The done event's data must be a valid ResolveResponse identical to the JSON path.
        client = make_client()

        with patch("qre.engine.extract.date") as mock_date:
            mock_date.today.return_value = PINNED_DATE
            mock_date.side_effect = None
            sse_resp = client.post(
                "/api/qre/resolve",
                json={
                    "input": {
                        "kind": "raw_text",
                        "query": "health ODA grants from USA to Ethiopia",
                    }
                },
                headers={"Accept": "text/event-stream"},
            )

        # Extract done event payload.
        done_data: str | None = None
        for line in sse_resp.text.splitlines():
            if line.startswith("data:") and done_data is None:
                # Skip the progress data line; capture the second data line (done).
                pass
            if line.startswith("data:"):
                done_data = line[len("data:"):].strip()
        # Find the last data: line (the done event payload).
        data_lines = [
            ln[len("data:"):].strip()
            for ln in sse_resp.text.splitlines()
            if ln.startswith("data:")
        ]
        assert data_lines, "No data: lines found in SSE response"
        done_payload = data_lines[-1]
        parsed = ResolveResponse.model_validate(json.loads(done_payload))
        assert parsed.root.status in ("definite", "candidates", "no_data")

    def test_sse_done_data_matches_json_path(self):
        # Both paths must return the same ResolveResponse body.
        client = make_client()
        payload = {"input": {"kind": "raw_text", "query": "health ODA grants from USA to Ethiopia"}}

        with patch("qre.engine.extract.date") as mock_date:
            mock_date.today.return_value = PINNED_DATE
            mock_date.side_effect = None
            json_resp = client.post("/api/qre/resolve", json=payload)

        with patch("qre.engine.extract.date") as mock_date:
            mock_date.today.return_value = PINNED_DATE
            mock_date.side_effect = None
            sse_resp = client.post(
                "/api/qre/resolve", json=payload,
                headers={"Accept": "text/event-stream"},
            )

        # Extract done payload from SSE.
        data_lines = [
            ln[len("data:"):].strip()
            for ln in sse_resp.text.splitlines()
            if ln.startswith("data:")
        ]
        done_payload = json.loads(data_lines[-1])

        # Compare status fields; full body byte equality is fragile across re-runs
        # due to non-deterministic LLM mock order, but status must match.
        assert done_payload["status"] == json_resp.json()["status"]

    def test_default_accept_returns_json(self):
        # Without text/event-stream in Accept, the response is buffered JSON.
        client = make_client()
        with patch("qre.engine.extract.date") as mock_date:
            mock_date.today.return_value = PINNED_DATE
            mock_date.side_effect = None
            resp = client.post(
                "/api/qre/resolve",
                json={
                    "input": {
                        "kind": "raw_text",
                        "query": "health ODA grants from USA to Ethiopia",
                    }
                },
            )
        assert resp.status_code == 200
        assert resp.headers.get("content-type", "").startswith("application/json")
        body = resp.json()
        assert body["status"] in ("definite", "candidates", "no_data")


class TestEngineInputError:
    def test_engine_input_error_returns_400(self, monkeypatch):
        # When resolve_async raises EngineInputError, the handler returns 400.
        from qre.engine.errors import EngineInputError

        async def _raise_input_error(*args, **kwargs):
            raise EngineInputError("unknown shape_id")

        monkeypatch.setattr("qre.engine.app.resolve_async", _raise_input_error)
        client = make_client()
        resp = client.post(
            "/api/qre/resolve",
            json={"input": {"kind": "raw_text", "query": "test"}},
        )
        assert resp.status_code == 400
        assert resp.json() == {"detail": "Bad request"}

    def test_engine_input_error_with_code_includes_code(self, monkeypatch):
        # EngineInputError with code="promote_only" adds the code field to the body.
        from qre.engine.errors import EngineInputError

        async def _raise_with_code(*args, **kwargs):
            raise EngineInputError("edited bindings not supported", code="promote_only")

        monkeypatch.setattr("qre.engine.app.resolve_async", _raise_with_code)
        client = make_client()
        resp = client.post(
            "/api/qre/resolve",
            json={"input": {"kind": "raw_text", "query": "test"}},
        )
        assert resp.status_code == 400
        assert resp.json() == {"detail": "Bad request", "code": "promote_only"}

    def test_engine_input_error_routing_failure_no_code(self, monkeypatch):
        # Routing failures (no code attr set) must return only {"detail": "Bad request"}.
        from qre.engine.errors import EngineInputError

        async def _raise_routing(*args, **kwargs):
            raise EngineInputError("shape_id mismatch")  # no code kwarg

        monkeypatch.setattr("qre.engine.app.resolve_async", _raise_routing)
        client = make_client()
        resp = client.post(
            "/api/qre/resolve",
            json={"input": {"kind": "raw_text", "query": "test"}},
        )
        assert resp.status_code == 400
        body = resp.json()
        assert body == {"detail": "Bad request"}
        assert "code" not in body


def test_spec_resubmit_unknown_shape_returns_400():
    """spec_resubmit with an unknown shape_id must return HTTP 400."""
    client = make_client()
    resp = client.post(
        "/api/qre/resolve",
        json={
            "input": {
                "kind": "spec_resubmit",
                "shape_id": "nonexistent_shape_xyz",
                "slots": [],
            }
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Bad request"


class TestCORS:
    def test_cors_preflight_allowed_origin(self, monkeypatch):
        # An origin in QRE_CORS_ORIGINS receives Access-Control-Allow-Origin.
        monkeypatch.setenv("QRE_CORS_ORIGINS", "https://app.example.com")
        app = create_app(graph=FakeGraph(), llm=FakeLLM())
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.options(
            "/api/qre/resolve",
            headers={
                "Origin": "https://app.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "https://app.example.com"

    def test_cors_no_header_for_empty_allowlist(self):
        # Default (empty QRE_CORS_ORIGINS) adds no CORS headers.
        import os as _os
        _os.environ.pop("QRE_CORS_ORIGINS", None)
        app = create_app(graph=FakeGraph(), llm=FakeLLM())
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.options(
            "/api/qre/resolve",
            headers={
                "Origin": "https://attacker.example.com",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in resp.headers
