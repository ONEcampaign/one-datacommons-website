"""FastAPI app tests using TestClient."""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from qre.engine.app import create_app
from qre.engine.config import ENGINE_BUILD_ID, QRE_MAX_QUERY_CHARS
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

        class ErrorGraph:
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
            def observation_facets(self, *, stat_var, entity):
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

        class _BoomGraph:
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
            def observation_facets(self, *, stat_var, entity):
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
