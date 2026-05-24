"""FastAPI application: routes, lifespan, error handlers.

Routes match the nginx-proxied URLs verbatim so no path rewrite is required:

    POST /api/dc-search         — default (extraction + multi-variable fan-out)
    POST /api/dc-search/simple  — simple (single-variable, no extraction LLM call)
    GET  /api/dc-search/healthz — liveness probe (excluded from OpenAPI)
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from datacommons_client.utils.error_handling import APIError as DCAPIError
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from dc_search import config, llm, pipeline, retrieval
from dc_search.pipeline import PipelineResult
from dc_search.predicate import AnswerCollection, AskClarification
from dc_search.telemetry import TelemetryLLMUsage

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Request / response Pydantic models
# ---------------------------------------------------------------------------


class SearchRequest(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=4000,
        description="Natural-language query.",
    )
    # No `model` override field — single env-configured model; see DC_SEARCH_MODEL.


class TelemetryBlock(BaseModel):
    llm_usage: list[TelemetryLLMUsage]
    n_candidates: int
    n_shapes: int
    terminated_by: Literal["answer", "ask", "no_candidates", "error"]
    truncated: bool = False


class SearchResponse(BaseModel):
    query: str
    answers: list[AnswerCollection] = Field(
        default_factory=list,
        description=(
            "One AnswerCollection per extracted variable (default endpoint), "
            "or at most one (simple endpoint). Empty iff `ask` is set."
        ),
    )
    ask: AskClarification | None = Field(
        default=None,
        description=(
            "Set when the pipeline could not produce answers and needs clarification. "
            "Mutually exclusive with non-empty `answers`."
        ),
    )
    elapsed_s: float
    telemetry: TelemetryBlock


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


def _to_response(result: PipelineResult) -> SearchResponse:
    """Map a PipelineResult to a SearchResponse."""
    telemetry = TelemetryBlock(
        llm_usage=result.llm_usage,
        n_candidates=result.n_candidates,
        n_shapes=result.n_shapes,
        terminated_by=result.terminated_by,
        truncated=result.truncated,
    )
    return SearchResponse(
        query=result.query,
        answers=result.answers,
        ask=result.ask,
        elapsed_s=result.elapsed_s,
        telemetry=telemetry,
    )


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

_ROUTE_TIMEOUT_S: float = 25.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_event_loop()
    # Bound the default executor for asyncio.to_thread offloads.
    # The default pool (min(32, os.cpu_count()+4)) is insufficient for
    # 4 workers × 8 fan-out semaphore × ~3 to_thread calls per stage.
    loop.set_default_executor(ThreadPoolExecutor(max_workers=32, thread_name_prefix="dc-search-io"))
    # Construct singletons only after the event loop is running; constructing
    # them at import time can bind the internal httpx.AsyncClient to the wrong loop.
    _ = llm.get_client()
    _ = retrieval.get_client()
    # Validate DC_API_URL allowlist at startup; raises ValueError on bad config.
    _ = config.load_config()
    yield
    # No explicit teardown — OS reaps threads on process exit.


# ---------------------------------------------------------------------------
# App + error handlers
# ---------------------------------------------------------------------------

app = FastAPI(
    title="DC-Search",
    description="Statistical-variable search for DataCommons.",
    version="0.1.0",
    lifespan=lifespan,
)

# Permissive CORS so browser-based clients (comparison UIs, notebooks) can call
# the API cross-origin. Matches the mixer's existing `*` CORS posture in this
# deployment; tighten allow_origins if this is ever exposed beyond internal use.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ValueError)
async def _value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    logger.warning("Bad request", exc_info=True)
    return JSONResponse(status_code=400, content={"detail": "Bad request."})


@app.exception_handler(asyncio.TimeoutError)
async def _timeout_handler(request: Request, exc: asyncio.TimeoutError) -> JSONResponse:
    logger.warning("Request timed out")
    return JSONResponse(status_code=504, content={"detail": "Request timed out."})


@app.exception_handler(httpx.RequestError)
async def _httpx_request_error_handler(request: Request, exc: httpx.RequestError) -> JSONResponse:
    logger.warning("httpx connection error", exc_info=True)
    return JSONResponse(status_code=503, content={"detail": "Upstream service unavailable."})


@app.exception_handler(httpx.HTTPStatusError)
async def _httpx_status_handler(request: Request, exc: httpx.HTTPStatusError) -> JSONResponse:
    status_code = exc.response.status_code
    if status_code >= 500:
        logger.error("Mixer 5xx: %s", exc)
        return JSONResponse(status_code=503, content={"detail": "Upstream service unavailable."})
    logger.warning("Mixer 4xx: %s", exc)
    return JSONResponse(
        status_code=502, content={"detail": "Bad gateway — upstream rejected request."}
    )


@app.exception_handler(DCAPIError)
async def _dc_api_error_handler(request: Request, exc: DCAPIError) -> JSONResponse:
    # datacommons-client is requests-based; DCConnectionError/DCStatusError both
    # subclass APIError. status_code is None for connection-level failures.
    status_code = exc.status_code
    if status_code is None:
        logger.warning("DataCommons connection error", exc_info=True)
        return JSONResponse(status_code=503, content={"detail": "Upstream service unavailable."})
    if status_code >= 500:
        logger.error("DataCommons 5xx: %s", exc)
        return JSONResponse(status_code=503, content={"detail": "Upstream service unavailable."})
    logger.warning("DataCommons 4xx: %s", exc)
    return JSONResponse(
        status_code=502, content={"detail": "Bad gateway — upstream rejected request."}
    )


# google.genai.errors is imported lazily to avoid import-time side effects
# when running tests without the genai library fully initialised.
try:
    from google.genai import errors as _genai_errors

    @app.exception_handler(_genai_errors.APIError)
    async def _genai_api_error_handler(
        request: Request, exc: _genai_errors.APIError
    ) -> JSONResponse:
        logger.error("Gemini APIError: %s", exc)
        return JSONResponse(status_code=503, content={"detail": "LLM service unavailable."})

except Exception:
    pass  # genai not installed in test environment; handler omitted gracefully


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post("/api/dc-search")
async def search(req: SearchRequest) -> SearchResponse:
    result = await asyncio.wait_for(pipeline.run_default(req.query), timeout=_ROUTE_TIMEOUT_S)
    return _to_response(result)


@app.post("/api/dc-search/simple")
async def search_simple(req: SearchRequest) -> SearchResponse:
    result = await asyncio.wait_for(pipeline.run_simple(req.query), timeout=_ROUTE_TIMEOUT_S)
    return _to_response(result)


@app.get("/api/dc-search/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
