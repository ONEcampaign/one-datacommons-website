"""FastAPI application: routes, lifespan, error handlers.

Routes match the nginx-proxied URLs verbatim so no path rewrite is required:

    POST /api/dc-search         — default (extraction + multi-variable fan-out)
    POST /api/dc-search/simple  — simple (single-variable, no extraction LLM call)
    GET  /api/dc-search/healthz — liveness probe (excluded from OpenAPI)
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from datacommons_client.utils.error_handling import APIError as DCAPIError
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from dc_search import config, llm, pipeline, retrieval
from dc_search.events import Done, Error, Event, serialize_sse
from dc_search.interpretation import QueryInterpretation
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
    interpretation: QueryInterpretation | None = Field(
        default=None,
        description=(
            "Buffered query interpretation assembled from the ``interpretation`` and "
            "``places`` SSE events.  ``None`` for simple-endpoint responses when no "
            "interpretation event was emitted."
        ),
    )


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
        interpretation=result.interpretation,
    )


def _classify_exception(exc: BaseException) -> tuple[int, str]:
    """Map an exception to (status_code, sanitized_detail).

    Single source of truth for both the JSON @exception_handlers and the SSE
    terminal-error path. Mirrors the existing taxonomy:
      ValueError                      -> (400, "Bad request.")
      asyncio.TimeoutError            -> (504, "Request timed out.")
      httpx.RequestError              -> (503, "Upstream service unavailable.")
      httpx.HTTPStatusError (>=500)   -> (503, "Upstream service unavailable.")
      httpx.HTTPStatusError (4xx)     -> (502, "Bad gateway — upstream rejected request.")
      DCAPIError (None/>=500)         -> (503, "Upstream service unavailable.")
      DCAPIError (4xx)                -> (502, "Bad gateway — upstream rejected request.")
      genai APIError                  -> (503, "LLM service unavailable.")
      <anything else>                 -> (500, "Internal server error.")
    """
    if isinstance(exc, ValueError):
        return 400, "Bad request."
    if isinstance(exc, asyncio.TimeoutError):
        return 504, "Request timed out."
    if isinstance(exc, httpx.RequestError):
        return 503, "Upstream service unavailable."
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code >= 500:
            return 503, "Upstream service unavailable."
        return 502, "Bad gateway — upstream rejected request."
    if isinstance(exc, DCAPIError):
        status_code = exc.status_code
        if status_code is None or status_code >= 500:
            return 503, "Upstream service unavailable."
        return 502, "Bad gateway — upstream rejected request."
    # Check genai lazily to mirror the lazy import in the exception handler below.
    try:
        from google.genai import errors as _genai_errors

        if isinstance(exc, _genai_errors.APIError):
            return 503, "LLM service unavailable."
    except Exception:
        pass
    return 500, "Internal server error."


def _accepts_sse(request: Request) -> bool:
    """True iff the client explicitly requests text/event-stream with q>0.

    A real parse (not `"text/event-stream" in accept`): split on commas, strip
    params, honor an explicit q=0 as "not acceptable". Only an explicit
    `text/event-stream` token counts — `text/*`, `*/*`, and absent Accept all
    fall through to the buffered JSON branch. SSE is strictly opt-in so that
    default clients (browser fetch, curl, TestClient) stay on JSON.
    """
    raw = request.headers.get("accept")
    if not raw:
        return False
    for part in raw.split(","):
        token, _, params = part.strip().partition(";")
        media = token.strip().lower()
        if media != "text/event-stream":
            continue
        q = 1.0
        for p in params.split(";"):
            k, _, val = p.strip().partition("=")
            if k.strip().lower() == "q":
                try:
                    q = float(val)
                except ValueError:
                    q = 0.0
        if q > 0:
            return True
    return False


# Module-level sentinels for the SSE merge queue.
_HEARTBEAT = object()  # signals a 10s tick; serialized as `: ping\n\n`
_SENTINEL = object()  # signals the producer finished


async def _event_source(stream: AsyncIterator[Event]) -> AsyncIterator[str]:
    """Merge the event generator with a heartbeat ticker into an SSE byte stream.

    Owns NO deadline logic (the generator emits Done(timed_out=True) — finding B)
    and NO disconnect poller (Starlette 1.0.1 cancels this body task on
    http.disconnect — finding A). Responsibilities: drain the queue, turn events
    into SSE frames, interleave heartbeat comments, stop after the one terminal
    event, and cancel both helper tasks on exit (normal OR CancelledError).
    """
    queue: asyncio.Queue[object] = asyncio.Queue()

    async def produce() -> None:
        try:
            async for ev in stream:
                await queue.put(ev)
        except Exception as exc:
            # Catch Exception, NOT BaseException: CancelledError must propagate so
            # this task actually cancels on client-disconnect (Starlette cancels the
            # body task → us). Swallowing it would emit a bogus `error` to a client
            # that is already gone. A real pipeline failure becomes a terminal Error
            # event here (the generator itself never yields Error — see Slice 2).
            _, detail = _classify_exception(exc)
            await queue.put(Error(detail=detail))
        finally:
            # Runs on normal finish AND on cancellation (after which CancelledError
            # re-raises, marking this task cancelled). The SENTINEL still lands so
            # the consumer loop terminates if it is not itself being cancelled.
            await queue.put(_SENTINEL)

    async def heartbeat() -> None:
        while True:
            await asyncio.sleep(10)
            await queue.put(_HEARTBEAT)

    prod_task = asyncio.create_task(produce())
    hb_task = asyncio.create_task(heartbeat())
    try:
        while True:
            item = await queue.get()
            if item is _SENTINEL:
                return
            if item is _HEARTBEAT:
                yield ": ping\n\n"
                continue
            yield serialize_sse(item)  # type: ignore[arg-type]  # item is a real Event here
            if isinstance(item, (Done, Error)):
                return  # exactly one terminal event
    finally:
        # Normal completion OR client-disconnect (CancelledError raised into the
        # loop by Starlette). Cancel + await both helpers so neither outlives the
        # response; cancelling prod_task unwinds the generator, whose own `finally`
        # cancels the in-flight fan-out tasks (Slice 2 step 9). One cancellation path.
        for t in (prod_task, hb_task):
            t.cancel()
        await asyncio.gather(prod_task, hb_task, return_exceptions=True)


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
    status, detail = _classify_exception(exc)
    return JSONResponse(status_code=status, content={"detail": detail})


@app.exception_handler(asyncio.TimeoutError)
async def _timeout_handler(request: Request, exc: asyncio.TimeoutError) -> JSONResponse:
    logger.warning("Request timed out")
    status, detail = _classify_exception(exc)
    return JSONResponse(status_code=status, content={"detail": detail})


@app.exception_handler(httpx.RequestError)
async def _httpx_request_error_handler(request: Request, exc: httpx.RequestError) -> JSONResponse:
    logger.warning("httpx connection error", exc_info=True)
    status, detail = _classify_exception(exc)
    return JSONResponse(status_code=status, content={"detail": detail})


@app.exception_handler(httpx.HTTPStatusError)
async def _httpx_status_handler(request: Request, exc: httpx.HTTPStatusError) -> JSONResponse:
    if exc.response.status_code >= 500:
        logger.error("Mixer 5xx: %s", exc)
    else:
        logger.warning("Mixer 4xx: %s", exc)
    status, detail = _classify_exception(exc)
    return JSONResponse(status_code=status, content={"detail": detail})


@app.exception_handler(DCAPIError)
async def _dc_api_error_handler(request: Request, exc: DCAPIError) -> JSONResponse:
    # datacommons-client is requests-based; DCConnectionError/DCStatusError both
    # subclass APIError. status_code is None for connection-level failures.
    sc = exc.status_code
    if sc is None or sc >= 500:
        logger.warning("DataCommons connection/5xx error", exc_info=True)
    else:
        logger.warning("DataCommons 4xx: %s", exc)
    status, detail = _classify_exception(exc)
    return JSONResponse(status_code=status, content={"detail": detail})


# google.genai.errors is imported lazily to avoid import-time side effects
# when running tests without the genai library fully initialised.
try:
    from google.genai import errors as _genai_errors

    @app.exception_handler(_genai_errors.APIError)
    async def _genai_api_error_handler(
        request: Request, exc: _genai_errors.APIError
    ) -> JSONResponse:
        logger.error("Gemini APIError: %s", exc)
        status, detail = _classify_exception(exc)
        return JSONResponse(status_code=status, content={"detail": detail})

except Exception:
    pass  # genai not installed in test environment; handler omitted gracefully


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.post("/api/dc-search", response_model=SearchResponse)
async def search(req: SearchRequest, request: Request) -> Response:
    if _accepts_sse(request):
        return StreamingResponse(
            _event_source(pipeline.stream_default(req.query)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )
    result = await asyncio.wait_for(pipeline.run_default(req.query), timeout=_ROUTE_TIMEOUT_S)
    return _to_response(result)


@app.post("/api/dc-search/simple", response_model=SearchResponse)
async def search_simple(req: SearchRequest, request: Request) -> Response:
    if _accepts_sse(request):
        return StreamingResponse(
            _event_source(pipeline.stream_simple(req.query)),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )
    result = await asyncio.wait_for(pipeline.run_simple(req.query), timeout=_ROUTE_TIMEOUT_S)
    return _to_response(result)


@app.get("/api/dc-search/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}
