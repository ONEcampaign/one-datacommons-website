"""QRE engine core: async pipeline orchestration.

Main entry points:
  resolve_async(request, *, graph=None, llm=None) → ResolveResponse (async)
  resolve(request) → ResolveResponse (sync wrapper)

Pipeline: extract → recall → shape → bind → materialise → ground → assemble.
The per-variable body (recall through answer) lives in regions.resolve_variable.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from concurrent.futures import ThreadPoolExecutor

from qre.engine.assemble import (
    assemble_no_data,
    make_diagnostics,
    make_pipeline_step,
    make_query_echo,
    now_ms,
)
from qre.engine.config import (
    ENGINE_BUILD_ID,
    QRE_MAX_CANDIDATES,
    QRE_MAX_VARIABLES,
    QRE_SEAM_DEFAULT,
)
from qre.engine.conjoin import (
    VARIABLES_CLAMPED,
    assemble_region,
    combine_regions,
)
from qre.engine.errors import EngineInfraError
from qre.engine.extract import Extraction, dates_to_request, extract
from qre.engine.graph import EngineGraphClient, LiveGraphClient
from qre.engine.llm import LLM
from qre.engine.regions import RegionResult, resolve_variable
from qre.models import (
    QueryEcho,
    ResolveRequest,
    ResolveResponse,
    Warning,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


async def _resolve_pipeline(
    request: ResolveRequest,
    *,
    graph: EngineGraphClient,
    llm: LLM,
    start_ms: int,
) -> ResolveResponse:
    """Core pipeline body. Graph and LLM are always provided; lifecycle is the caller's concern."""
    inp = request.input

    include_sentence: bool
    if request.options and request.options.include_sentence is not None:
        include_sentence = request.options.include_sentence
    else:
        include_sentence = False

    if inp.kind != "raw_text":
        # spec_resubmit → not yet implemented; parsed → app layer rejects with 400
        return _quick_no_data(
            reason="variable_not_resolved",
            query="",
            engine_build=ENGINE_BUILD_ID,
            start_ms=start_ms,
            include_sentence=include_sentence,
        )

    query: str = inp.query  # type: ignore[union-attr]

    # Empty/whitespace query check
    if not query.strip():
        echo = make_query_echo(query, [], extract_skipped=True)
        diag = make_diagnostics(ENGINE_BUILD_ID, [], {}, now_ms() - start_ms)
        return assemble_no_data(
            "variable_not_resolved", echo, diag, include_sentence=include_sentence
        )

    # Seam flag from request options or config default
    pac: bool
    if request.options and request.options.place_as_constraint is not None:
        pac = request.options.place_as_constraint
    else:
        pac = QRE_SEAM_DEFAULT

    # --- Step: extract ---
    t0 = now_ms()
    extraction: Extraction = await extract(query, llm=llm)
    timing_extract = now_ms() - t0
    extract_step = make_pipeline_step("extract", ran=True, ms=timing_extract)

    if not extraction.variables:
        echo = make_query_echo(query, [], extract_skipped=False)
        diag = make_diagnostics(
            ENGINE_BUILD_ID, [], {"extract": timing_extract}, now_ms() - start_ms
        )
        return assemble_no_data(
            "variable_not_resolved", echo, diag, include_sentence=include_sentence
        )

    entities = extraction.entities
    date_request = dates_to_request(extraction.dates)

    # Order-preserving dedupe: "GDP and GDP" → one variable
    unique: list[str] = list(dict.fromkeys(v.strip() for v in extraction.variables))

    # Clamp to QRE_MAX_VARIABLES; emit VARIABLES_CLAMPED when the list is truncated.
    clamp_warnings: list[Warning] = []
    if len(unique) > QRE_MAX_VARIABLES:
        clamp_warnings.append(Warning(
            code=VARIABLES_CLAMPED,
            severity="warn",
            message=f"Query named {len(unique)} variables; resolved the first {QRE_MAX_VARIABLES}.",
        ))
        unique = unique[:QRE_MAX_VARIABLES]

    # N=1 direct bypass — never routes through the combiner (hard regression guard)
    if len(unique) == 1:
        region = await resolve_variable(
            unique[0],
            entities=entities,
            date_request=date_request,
            detect_query=query,
            role_query=query,
            pac=pac,
            graph=graph,
            llm=llm,
            base_steps=[extract_step],
            base_timing={"extract": timing_extract},
        )
        return assemble_region(
            region,
            query=query,
            variable_texts=unique,
            extra_warnings=clamp_warnings,
            start_ms=start_ms,
            engine_build=ENGINE_BUILD_ID,
            include_sentence=include_sentence,
            max_candidates=QRE_MAX_CANDIDATES,
        )

    # N≥2: resolve each variable concurrently; region exceptions map to no_data parts.
    results = await asyncio.gather(
        *[
            resolve_variable(
                v,
                entities=entities,
                date_request=date_request,
                detect_query=v,
                role_query=query,
                pac=pac,
                graph=graph,
                llm=llm,
                base_steps=[extract_step],
                base_timing={"extract": timing_extract},
            )
            for v in unique
        ],
        return_exceptions=True,
    )

    regions: list[RegionResult] = []
    for i, r in enumerate(results):
        if isinstance(r, RegionResult):
            regions.append(dataclasses.replace(r, earliest_index=i))
        elif isinstance(r, EngineInfraError):
            # Infrastructure failures (transport, timeout, LLM parse) must fail loud,
            # matching the N=1 path's behaviour and the documented HTTP 500 contract.
            raise r
        else:
            # A non-infra region exception → no_data part; never fails the whole response.
            regions.append(RegionResult(
                variable_text=unique[i],
                status="no_data",
                specs=(),
                no_data_reason="variable_not_resolved",
                warnings=(),
                timing_by_step={},
                earliest_index=i,
            ))

    return combine_regions(
        regions,
        query=query,
        variable_texts=unique,
        extra_warnings=clamp_warnings,
        start_ms=start_ms,
        engine_build=ENGINE_BUILD_ID,
        include_sentence=include_sentence,
        max_candidates=QRE_MAX_CANDIDATES,
    )


async def resolve_async(
    request: ResolveRequest,
    *,
    graph: EngineGraphClient | None = None,
    llm: LLM | None = None,
) -> ResolveResponse:
    """Async pipeline: extract → recall → bind → materialise → assemble.

    Graph and LLM are injected; when None, live clients are built from env.

    Args:
        request: The typed ResolveRequest from the caller.
        graph: Graph client. When None, LiveGraphClient() is built (reads
            QRE_GRAPH_BASE from env).
        llm: LLM wrapper. When None, LLM() is built (reads GEMINI_API_KEY from env).

    Returns:
        A ResolveResponse (DefiniteResponse | CandidatesResponse | NoDataResponse).
    """
    start_ms = now_ms()
    owns_graph = graph is None
    _graph = graph if graph is not None else LiveGraphClient()
    _llm = llm or LLM()

    try:
        return await _resolve_pipeline(
            request,
            graph=_graph,
            llm=_llm,
            start_ms=start_ms,
        )
    finally:
        if owns_graph and hasattr(_graph, "close"):
            _graph.close()  # ty: ignore[call-non-callable]  # hasattr


def _quick_no_data(
    *,
    reason: str,
    query: str,
    engine_build: str,
    start_ms: int,
    include_sentence: bool = False,
) -> ResolveResponse:
    """Build a no_data response without any LLM or graph calls."""
    total_ms = now_ms() - start_ms
    echo = QueryEcho(
        entry_path="raw_text",
        raw_query=query or None,
        normalized_query=None,
        variable_text=[],
        extract_skipped=True,
    )
    diag = make_diagnostics(engine_build, [], {}, total_ms)
    return assemble_no_data(reason, echo, diag, include_sentence=include_sentence)


def resolve(request: ResolveRequest) -> ResolveResponse:
    """Sync wrapper around resolve_async.

    Builds LiveGraphClient and LLM from environment variables.
    For dependency injection (tests), use resolve_async(request, graph=..., llm=...).

    Loop-safe: callable both standalone and from within a running event loop
    (e.g. the Langfuse experiment runner, which awaits the task inside its loop).
    When a loop is already running, the pipeline runs in a worker thread with its
    own loop so asyncio.run does not nest.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(resolve_async(request))
    # Serial offload (one thread, blocks caller). If concurrent item resolution
    # is needed in the future, switch to an async task seam.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(resolve_async(request))).result()
