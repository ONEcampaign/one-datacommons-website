"""Tests for app.py — FastAPI routes, error handlers, lifespan."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Environment setup — must happen before dc_search is imported.
# conftest.py already sets GEMINI_API_KEY.  We also need DC_API_URL to satisfy
# config.load_config()'s localhost allowlist at lifespan startup.
# ---------------------------------------------------------------------------

os.environ.setdefault("DC_API_URL", "http://localhost:8081/core/api/v2")


# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------

from dc_search.pipeline import PipelineResult
from dc_search.predicate import AnswerCollection, AskClarification, Predicate
from dc_search.telemetry import TelemetryLLMUsage, Usage

_PREDICATE = Predicate(
    population_type="Person",
    measured_property="lifeExpectancy",
    constraints={},
)

_ANSWER = AnswerCollection(
    predicate=_PREDICATE,
    sv_set=["LifeExpectancy_Person"],
    confidence="high",
)

_USAGE = Usage(input_tokens=100, output_tokens=20, model="gemini-flash-lite-latest")

_PIPELINE_RESULT_ANSWER = PipelineResult(
    query="life expectancy in Kenya",
    answers=[_ANSWER],
    ask=None,
    elapsed_s=0.42,
    n_candidates=10,
    n_shapes=3,
    terminated_by="answer",
    llm_usage=[
        TelemetryLLMUsage(
            step="slot_bind", input_tokens=100, output_tokens=20, model="gemini-flash-lite-latest"
        )
    ],
    truncated=False,
)

_PIPELINE_RESULT_ASK = PipelineResult(
    query="life expectancy in Kenya",
    answers=[],
    ask=AskClarification(reason="no_candidates", message="No candidates found."),
    elapsed_s=0.1,
    n_candidates=0,
    n_shapes=0,
    terminated_by="no_candidates",
    llm_usage=[],
    truncated=False,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_lifespan_singletons(monkeypatch):
    """Prevent lifespan from constructing real genai/DC clients."""
    import dc_search.llm as _llm
    import dc_search.retrieval as _retrieval

    monkeypatch.setattr(_llm, "get_client", lambda: MagicMock())
    monkeypatch.setattr(_retrieval, "get_client", lambda: MagicMock())


@pytest.fixture
def client(monkeypatch):
    """TestClient with pipeline mocked to return a successful answer."""
    import dc_search.pipeline as _pipeline

    monkeypatch.setattr(_pipeline, "run_simple", AsyncMock(return_value=_PIPELINE_RESULT_ANSWER))
    monkeypatch.setattr(_pipeline, "run_default", AsyncMock(return_value=_PIPELINE_RESULT_ANSWER))

    from dc_search.app import app

    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# (a) POST /api/dc-search/simple — happy path → 200
# ---------------------------------------------------------------------------


def test_search_simple_happy_path(client):
    resp = client.post(
        "/api/dc-search/simple",
        json={"query": "life expectancy in Kenya"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["query"] == "life expectancy in Kenya"
    assert len(body["answers"]) == 1
    assert body["ask"] is None
    assert "elapsed_s" in body
    assert "telemetry" in body
    tel = body["telemetry"]
    assert tel["terminated_by"] == "answer"
    assert isinstance(tel["llm_usage"], list)
    assert len(tel["llm_usage"]) == 1
    assert tel["llm_usage"][0]["step"] == "slot_bind"


# ---------------------------------------------------------------------------
# (b) POST /api/dc-search — happy path → 200
# ---------------------------------------------------------------------------


def test_search_default_happy_path(monkeypatch):
    import dc_search.pipeline as _pipeline

    result_two = PipelineResult(
        query="life expectancy and population in Kenya",
        answers=[
            _ANSWER,
            AnswerCollection(
                predicate=Predicate(
                    population_type="Person",
                    measured_property="count",
                    constraints={},
                ),
                sv_set=["Count_Person"],
                confidence="medium",
                variable_label="population",
            ),
        ],
        ask=None,
        elapsed_s=0.9,
        n_candidates=20,
        n_shapes=4,
        terminated_by="answer",
        llm_usage=[
            TelemetryLLMUsage(
                step="extract", input_tokens=50, output_tokens=10, model="gemini-flash-lite-latest"
            ),
            TelemetryLLMUsage(
                step="slot_bind",
                input_tokens=100,
                output_tokens=20,
                model="gemini-flash-lite-latest",
            ),
            TelemetryLLMUsage(
                step="slot_bind",
                input_tokens=100,
                output_tokens=20,
                model="gemini-flash-lite-latest",
            ),
        ],
        truncated=False,
    )

    monkeypatch.setattr(_pipeline, "run_simple", AsyncMock(return_value=_PIPELINE_RESULT_ANSWER))
    monkeypatch.setattr(_pipeline, "run_default", AsyncMock(return_value=result_two))

    from dc_search.app import app

    with TestClient(app) as c:
        resp = c.post(
            "/api/dc-search",
            json={"query": "life expectancy and population in Kenya"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["answers"]) == 2
    steps = [u["step"] for u in body["telemetry"]["llm_usage"]]
    assert steps.count("extract") == 1
    assert steps.count("slot_bind") == 2


# ---------------------------------------------------------------------------
# (c) Empty query → 422 (FastAPI validation)
# ---------------------------------------------------------------------------


def test_empty_query_returns_422(client):
    resp = client.post("/api/dc-search/simple", json={"query": ""})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# (d) Over-long query (> 4000 chars) → 422 (FastAPI validation)
# ---------------------------------------------------------------------------


def test_overlength_query_returns_422(client):
    resp = client.post(
        "/api/dc-search/simple",
        json={"query": "x" * 4001},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# (e) google.genai.errors.APIError from pipeline → 503
# ---------------------------------------------------------------------------


def test_genai_api_error_returns_503(monkeypatch):
    import dc_search.pipeline as _pipeline

    try:
        from google.genai import errors as _genai_errors

        api_error_cls = _genai_errors.APIError
    except Exception:
        pytest.skip("google.genai not available in this environment")

    async def _raise_api_error(query):
        raise api_error_cls(
            503,
            {"error": {"message": "quota exceeded", "status": "UNAVAILABLE", "code": 503}},
        )

    monkeypatch.setattr(_pipeline, "run_simple", _raise_api_error)
    monkeypatch.setattr(_pipeline, "run_default", _raise_api_error)

    from dc_search.app import app

    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.post("/api/dc-search/simple", json={"query": "life expectancy"})
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# (e2) datacommons-client APIError from pipeline → mapped upstream error
# ---------------------------------------------------------------------------


def test_dc_connection_error_returns_503(monkeypatch):
    """A network-level DCConnectionError (status_code None) maps to 503."""
    from datacommons_client.utils.error_handling import DCConnectionError

    import dc_search.pipeline as _pipeline

    async def _raise(query):
        raise DCConnectionError(message="mixer unreachable")

    monkeypatch.setattr(_pipeline, "run_simple", _raise)
    monkeypatch.setattr(_pipeline, "run_default", _raise)

    from dc_search.app import app

    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.post("/api/dc-search/simple", json={"query": "life expectancy"})
    assert resp.status_code == 503


def test_dc_status_error_4xx_returns_502(monkeypatch):
    """A DCStatusError carrying a 4xx response maps to 502."""
    from datacommons_client.utils.error_handling import DCStatusError

    import dc_search.pipeline as _pipeline

    response = MagicMock()
    response.status_code = 400

    async def _raise(query):
        raise DCStatusError(response=response, message="mixer rejected request")

    monkeypatch.setattr(_pipeline, "run_simple", _raise)
    monkeypatch.setattr(_pipeline, "run_default", _raise)

    from dc_search.app import app

    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.post("/api/dc-search/simple", json={"query": "life expectancy"})
    assert resp.status_code == 502


# ---------------------------------------------------------------------------
# (f) asyncio.TimeoutError from pipeline → 504
# ---------------------------------------------------------------------------


def test_timeout_returns_504(monkeypatch):
    import dc_search.pipeline as _pipeline

    async def _raise_timeout(query):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(_pipeline, "run_simple", _raise_timeout)
    monkeypatch.setattr(_pipeline, "run_default", _raise_timeout)

    from dc_search.app import app

    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.post("/api/dc-search/simple", json={"query": "life expectancy"})
    assert resp.status_code == 504


# ---------------------------------------------------------------------------
# (g) GET /api/dc-search/healthz → 200 with {"status": "ok"}
# ---------------------------------------------------------------------------


def test_healthz(client):
    resp = client.get("/api/dc-search/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# (h) OpenAPI schema does NOT list /api/dc-search/healthz (include_in_schema=False)
# ---------------------------------------------------------------------------


def test_healthz_excluded_from_openapi(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    schema = resp.json()
    paths = schema.get("paths", {})
    assert "/api/dc-search/healthz" not in paths, (
        "healthz must be excluded from OpenAPI schema (include_in_schema=False)"
    )


# ---------------------------------------------------------------------------
# (i) ValueError from pipeline → 400 with fixed "Bad request."
# ---------------------------------------------------------------------------


def test_value_error_returns_bad_request(monkeypatch):
    """ValueError handler must return the fixed 'Bad request.' message, not the exception text."""
    import dc_search.pipeline as _pipeline

    async def _raise_value_error(query):
        raise ValueError("DC_API_URL must start with http://localhost: — INTERNAL_URL")

    monkeypatch.setattr(_pipeline, "run_simple", _raise_value_error)
    monkeypatch.setattr(_pipeline, "run_default", _raise_value_error)

    from dc_search.app import app

    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.post("/api/dc-search/simple", json={"query": "life expectancy"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["detail"] == "Bad request."
    # Must NOT leak the internal URL from the exception
    assert "INTERNAL_URL" not in body["detail"]
