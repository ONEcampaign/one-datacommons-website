"""FastAPI application factory for the QRE engine.

The app is created via create_app() so it can be instantiated with injected
graph/llm clients (useful in tests). For production, call create_app() with no
arguments; one shared LiveGraphClient is built at startup and closed at shutdown.

Endpoints:
  POST /api/qre/resolve   - main resolve endpoint
  GET  /api/qre/healthz   - health check

Error handling returns static bodies (no detail leakage for EngineInfraError).
Query length is checked before any LLM/graph call (QRE_MAX_QUERY_CHARS cap).
kind=parsed is rejected with HTTP 400 (not supported in v1).
"""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from qre.engine.config import ENGINE_BUILD_ID, QRE_MAX_QUERY_CHARS
from qre.engine.core import resolve_async
from qre.engine.errors import EngineInfraError
from qre.engine.graph import EngineGraphClient, LiveGraphClient
from qre.engine.llm import LLM
from qre.models import ResolveRequest, ResolveResponse

logger = logging.getLogger(__name__)


def create_app(
    *,
    graph: EngineGraphClient | None = None,
    llm: LLM | None = None,
) -> FastAPI:
    """Create and return the FastAPI application.

    Args:
        graph: Optional graph client to inject into the pipeline. When None,
            one shared LiveGraphClient is built at startup and closed on shutdown.
        llm: Optional LLM wrapper to inject. When None, LLM() is built per-request
            (reads GEMINI_API_KEY from env).

    Returns:
        A configured FastAPI application.
    """
    # When a graph is injected (e.g. in tests), use it directly without lifecycle management.
    # When none is injected, build a shared client for the process.
    _shared_graph: EngineGraphClient | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal _shared_graph
        if graph is None:
            _shared_graph = LiveGraphClient()
            logger.info("LiveGraphClient started")
        loop = asyncio.get_running_loop()
        loop.set_default_executor(
            ThreadPoolExecutor(max_workers=16, thread_name_prefix="qre-io")
        )
        try:
            yield
        finally:
            if _shared_graph is not None and hasattr(_shared_graph, "close"):
                _shared_graph.close()
                logger.info("LiveGraphClient closed")

    app = FastAPI(
        title="QRE Engine",
        version="1.0",
        lifespan=lifespan,
    )

    @app.exception_handler(EngineInfraError)
    async def infra_error_handler(request: Request, exc: EngineInfraError):
        logger.error("Engine infrastructure error: %s", exc, exc_info=exc)
        return JSONResponse(
            status_code=503,
            content={"detail": "Service temporarily unavailable"},
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        logger.error("Unhandled engine error: %s", exc, exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={"detail": "Service temporarily unavailable"},
        )

    @app.post("/api/qre/resolve")
    async def resolve_endpoint(body: ResolveRequest) -> ResolveResponse:
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
        return await resolve_async(body, graph=active_graph, llm=llm)

    @app.get("/api/qre/healthz")
    async def healthz():
        return {"status": "ok", "build": ENGINE_BUILD_ID}

    return app
