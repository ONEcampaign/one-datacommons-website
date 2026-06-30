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
    QRE_MAX_VARIABLE_CONCURRENCY,
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
from qre.engine.families import rule_for_shape_id
from qre.engine.graph import EngineGraphClient, LiveGraphClient
from qre.engine.llm import LLM, SupportsLLM
from qre.engine.regions import RegionResult, detect_set_ref, resolve_spec_resubmit, resolve_variable
from qre.models import (
    QueryEcho,
    ResolveRequest,
    ResolveResponse,
    SpecResubmitInput,
    Warning,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sum_usage(a: dict | None, b: dict | None) -> dict | None:
    """Sum two LLM usage dicts; returns None when both are None."""
    if a is None:
        return b
    if b is None:
        return a
    return {
        "input_tokens": a["input_tokens"] + b["input_tokens"],
        "output_tokens": a["output_tokens"] + b["output_tokens"],
        "cached_tokens": a["cached_tokens"] + b["cached_tokens"],
    }


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


async def _resolve_pipeline(
    request: ResolveRequest,
    *,
    graph: EngineGraphClient,
    llm: SupportsLLM,
    start_ms: int,
) -> ResolveResponse:
    """Core pipeline body. Graph and LLM are always provided; lifecycle is the caller's concern."""
    inp = request.input

    include_sentence: bool
    if request.options and request.options.include_sentence is not None:
        include_sentence = request.options.include_sentence
    else:
        include_sentence = False

    # Compute the effective max_candidates cap ONCE before the inp.kind dispatch
    # so both raw_text and spec_resubmit paths share the same value.
    _cap = (
        request.options.max_candidates
        if (request.options and request.options.max_candidates)
        else QRE_MAX_CANDIDATES
    )
    _max_cand = max(2, min(_cap, QRE_MAX_CANDIDATES))

    if inp.kind == "spec_resubmit":
        assert isinstance(inp, SpecResubmitInput)
        rule = rule_for_shape_id(shape_id=inp.shape_id)
        region = await asyncio.to_thread(resolve_spec_resubmit, inp=inp, rule=rule, graph=graph)
        echo = QueryEcho(
            entry_path="spec_resubmit",
            raw_query=None,
            normalized_query=None,
            variable_text=[region.variable_text] if region.variable_text else [],
            extract_skipped=True,
        )
        return assemble_region(
            region,
            query="",
            variable_texts=[region.variable_text] if region.variable_text else [],
            extra_warnings=[],
            start_ms=start_ms,
            engine_build=ENGINE_BUILD_ID,
            include_sentence=include_sentence,
            max_candidates=_max_cand,
            llm_usage=None,
            echo=echo,
        )

    if inp.kind != "raw_text":
        # parsed → app layer already rejects with 400 before reaching here
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
    extraction: Extraction
    extract_usage: dict | None
    extraction, extract_usage = await extract(query, llm=llm)
    timing_extract = now_ms() - t0
    extract_step = make_pipeline_step("extract", ran=True, ms=timing_extract)

    if not extraction.variables:
        echo = make_query_echo(query, [], extract_skipped=False)
        diag = make_diagnostics(
            ENGINE_BUILD_ID, [], {"extract": timing_extract}, now_ms() - start_ms,
            llm_usage=extract_usage,
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
        total_usage = _sum_usage(extract_usage, region.llm_usage)
        return assemble_region(
            region,
            query=query,
            variable_texts=unique,
            extra_warnings=clamp_warnings,
            start_ms=start_ms,
            engine_build=ENGINE_BUILD_ID,
            include_sentence=include_sentence,
            max_candidates=_max_cand,
            llm_usage=total_usage,
        )

    # Deduplicate entity resolution before fanning out. Each unique entity
    # name is resolved exactly once and reused across all variable legs.
    unique_entities = list(dict.fromkeys(entities))
    pre_resolved_dcids = await asyncio.gather(
        *[asyncio.to_thread(graph.resolve_entity, name) for name in unique_entities]
    )
    pre_resolved: dict[str, str] = {
        name: dcid
        for name, dcid in zip(unique_entities, pre_resolved_dcids, strict=True)
        if dcid is not None
    }

    # Semaphore caps per-request variable concurrency to prevent flooding
    # the graph/LLM with simultaneous calls from a large-N query.
    sem = asyncio.Semaphore(QRE_MAX_VARIABLE_CONCURRENCY)

    async def _guarded(v: str) -> RegionResult:
        async with sem:
            return await resolve_variable(
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
                pre_resolved=pre_resolved,
            )

    # N≥2: resolve each variable concurrently; region exceptions map to no_data parts.
    results = await asyncio.gather(
        *[_guarded(v) for v in unique],
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

    # Sum extract usage with per-variable bind usages.
    total_usage: dict | None = extract_usage
    for r in regions:
        total_usage = _sum_usage(total_usage, r.llm_usage)

    # combine_regions is otherwise pure, but the injected set_ref_for closure reads
    # the graph synchronously. Run the whole combiner in a worker thread so graph reads
    # inside collapse_same_shape do not block the event loop.
    return await asyncio.to_thread(
        combine_regions,
        regions,
        query=query,
        variable_texts=unique,
        extra_warnings=clamp_warnings,
        start_ms=start_ms,
        engine_build=ENGINE_BUILD_ID,
        include_sentence=include_sentence,
        max_candidates=_max_cand,
        llm_usage=total_usage,
        set_ref_for=lambda dcids: detect_set_ref(value_dcids=dcids, graph=graph),
    )


async def resolve_async(
    request: ResolveRequest,
    *,
    graph: EngineGraphClient | None = None,
    llm: SupportsLLM | None = None,
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
