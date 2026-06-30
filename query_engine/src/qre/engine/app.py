"""FastAPI application factory for the QRE engine.

The app is created via create_app() so it can be instantiated with injected
graph/llm clients (useful in tests). For production, call create_app() with no
arguments; one shared LiveGraphClient is built at startup and closed at shutdown.

Endpoints:
  POST /api/qre/resolve   - main resolve endpoint; supports Streamable HTTP content
                            negotiation (spec 2025-03-26): Accept text/event-stream
                            gets an SSE stream, else buffered JSON
  GET  /api/qre/healthz   - health check
  GET  /api/qre/schema    - runtime JSON Schema for ResolveRequest and ResolveResponse

Error handling returns static bodies (no detail leakage for EngineInfraError or
EngineInputError). Query length is checked before any LLM/graph call
(QRE_MAX_QUERY_CHARS cap). kind=parsed is rejected with HTTP 400 (not supported in v1).
"""
from __future__ import annotations

import asyncio
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse

import qre.engine.llm as _llm_mod
from qre.engine.config import (
    ENGINE_BUILD_ID,
    QRE_MAX_QUERY_CHARS,
    QRE_REQUEST_TIMEOUT_S,
    parse_cors_origins,
)
from qre.engine.core import resolve_async
from qre.engine.errors import EngineInfraError, EngineInputError, LLMInfraError
from qre.engine.graph import EngineGraphClient, LiveGraphClient
from qre.engine.llm import SupportsLLM
from qre.models import SCHEMA_VERSION, ResolveRequest, ResolveResponse

logger = logging.getLogger(__name__)


async def _resolve_sse(result: ResolveResponse):
    """Yield a two-event SSE stream per the Streamable HTTP pattern (spec 2025-03-26).

    The progress event is a fixed payload; it never echoes query text.
    Only the terminal done event carries the serialized ResolveResponse.
    """
    yield 'event: progress\ndata: {"status":"progress"}\n\n'
    yield f"event: done\ndata: {result.model_dump_json()}\n\n"


def create_app(
    *,
    graph: EngineGraphClient | None = None,
    llm: SupportsLLM | None = None,
) -> FastAPI:
    """Create and return the FastAPI application.

    Args:
        graph: Optional graph client to inject into the pipeline. When None,
            one shared LiveGraphClient is built at startup and closed on shutdown.
        llm: Optional LLM wrapper to inject. When None, LLM() is built per-request
            (reads GEMINI_API_KEY from env) and warm() is called at startup to
            fail fast on a missing key.

    Returns:
        A configured FastAPI application.
    """
    # When a graph is injected (e.g. in tests), use it directly without lifecycle management.
    # When none is injected, build a shared client for the process.
    _shared_graph: EngineGraphClient | None = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        nonlocal _shared_graph

        # Configure the qre parent logger once; the guard prevents duplicate
        # handlers when create_app() is called multiple times (e.g. in tests).
        _qre_log = logging.getLogger("qre")
        if not _qre_log.handlers:
            _h = logging.StreamHandler(sys.stdout)
            _h.setFormatter(
                logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")
            )
            _qre_log.addHandler(_h)
            _qre_log.setLevel(logging.INFO)
            _qre_log.propagate = False

        if graph is None:
            _shared_graph = LiveGraphClient()
            logger.info("LiveGraphClient started")

        # Warm the LLM singleton to fail fast on a missing GEMINI_API_KEY at
        # boot rather than under load on the first request. Skipped when an LLM
        # is injected (test mode) to keep tests free of key requirements.
        if llm is None:
            try:
                _llm_mod.warm()
            except LLMInfraError:
                logger.critical(
                    "LLM warm-up failed; check GEMINI_API_KEY", exc_info=True
                )
                raise

        loop = asyncio.get_running_loop()
        loop.set_default_executor(
            ThreadPoolExecutor(max_workers=16, thread_name_prefix="qre-io")
        )
        try:
            yield
        finally:
            if _shared_graph is not None and hasattr(_shared_graph, "close"):
                _shared_graph.close()  # ty: ignore[call-non-callable]  # hasattr
                logger.info("LiveGraphClient closed")

    app = FastAPI(
        title="QRE Engine",
        version="1.0",
        lifespan=lifespan,
    )

    # CORS allowlist read at create_app() time; empty default adds no CORS headers.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=parse_cors_origins(),
        allow_methods=["POST", "GET", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(EngineInfraError)
    async def infra_error_handler(_request: Request, exc: EngineInfraError):
        logger.error("Engine infrastructure error: %s", exc, exc_info=exc)
        # Upstream 4xx responses (e.g. graph returned 404) map to 502 Bad Gateway
        # so callers can distinguish client-side upstream errors from transient failures.
        upstream_status = getattr(exc, "upstream_status", None)
        if isinstance(upstream_status, int) and 400 <= upstream_status < 500:
            return JSONResponse(
                status_code=502,
                content={"detail": "Bad gateway: upstream returned an error"},
            )
        return JSONResponse(
            status_code=503,
            content={"detail": "Service temporarily unavailable"},
        )

    @app.exception_handler(EngineInputError)
    async def input_error_handler(_request: Request, exc: EngineInputError):
        # Static body; add "code" only when the exception carries one.
        # Routing failures carry no code so callers cannot probe which ids exist.
        content: dict[str, str] = {"detail": "Bad request"}
        if exc.code is not None:
            content["code"] = exc.code
        return JSONResponse(status_code=400, content=content)

    # asyncio.wait_for raises TimeoutError when the overall deadline expires.
    @app.exception_handler(asyncio.TimeoutError)
    async def timeout_error_handler(_request: Request, _exc: asyncio.TimeoutError):
        logger.error("Request timed out after %.1fs", QRE_REQUEST_TIMEOUT_S)
        return JSONResponse(
            status_code=504,
            content={"detail": "Request timed out"},
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(_request: Request, exc: Exception):
        logger.error("Unhandled engine error: %s", exc, exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "Service temporarily unavailable"},
        )

    @app.post("/api/qre/resolve")
    async def resolve_endpoint(body: ResolveRequest, request: Request) -> Response:
        # Reject kind=parsed (not supported in v1)
        if body.input.kind == "parsed":
            raise HTTPException(
                status_code=400,
                detail="kind=parsed not supported in v1",
            )

        # Query length check before any LLM/graph call
        if body.input.kind == "raw_text":
            q = body.input.query  # type: ignore[union-attr]
            if len(q) > QRE_MAX_QUERY_CHARS:
                raise HTTPException(
                    status_code=422,
                    detail=f"Query exceeds {QRE_MAX_QUERY_CHARS} characters",
                )

        active_graph = graph if graph is not None else _shared_graph
        # Enforce the overall request deadline to prevent sequential graph rounds
        # from stacking unbounded; asyncio.TimeoutError maps to 504.
        result: ResolveResponse = await asyncio.wait_for(
            resolve_async(body, graph=active_graph, llm=llm),
            timeout=QRE_REQUEST_TIMEOUT_S,
        )

        # Streamable HTTP content negotiation (spec 2025-03-26): if the caller
        # signals SSE acceptance, stream a progress event then the terminal done
        # event; otherwise return buffered JSON. Both paths carry the same body.
        accept = request.headers.get("accept", "")
        if "text/event-stream" in accept:
            return StreamingResponse(
                _resolve_sse(result),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        return JSONResponse(result.model_dump(mode="json"))

    @app.get("/api/qre/healthz")
    async def healthz():
        return {"status": "ok", "build": ENGINE_BUILD_ID}

    @app.get("/api/qre/schema")
    async def schema():
        # Contract-stable schema endpoint; distinct from FastAPI's auto-generated
        # /openapi.json (which is implementation-level). This endpoint is
        # version-aligned with schema_version and unauthenticated.
        return JSONResponse({
            "schema_version": SCHEMA_VERSION,
            "request": ResolveRequest.model_json_schema(),
            "response": ResolveResponse.model_json_schema(),
        })

    return app
