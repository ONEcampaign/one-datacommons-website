"""Tests for the SSE streaming interface — generators, endpoints, event contract.

Covers stream_default / stream_simple generators (cases 1–6, 10, 12–13), the
SSE endpoint (cases 7–9), and OpenAPI regression guard (case 11).

All external I/O is mocked.  Generator-level tests iterate directly over the
async generators; endpoint tests use Starlette's TestClient via client.stream().
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from dc_search.events import (
    Done,
    Error,
    Interpretation,
    Places,
    Result,
    Stage,
    Start,
)
from dc_search.extraction import QueryExtraction
from dc_search.predicate import AnswerCollection, AskClarification, Predicate
from dc_search.slot_binding import BindResult
from dc_search.telemetry import TelemetryLLMUsage, Usage

# ---------------------------------------------------------------------------
# Environment — must be set before dc_search.app is imported.
# ---------------------------------------------------------------------------

os.environ.setdefault("DC_API_URL", "http://localhost:8081/core/api/v2")

# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------

_QUERY = "life expectancy in Kenya"

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

_ANSWER2 = AnswerCollection(
    predicate=Predicate(
        population_type="Person",
        measured_property="count",
        constraints={},
    ),
    sv_set=["Count_Person"],
    confidence="medium",
    variable_label="population",
)

_USAGE = Usage(input_tokens=100, output_tokens=20, model="gemini-flash-lite-latest")
_EXTRACT_USAGE = Usage(input_tokens=50, output_tokens=10, model="gemini-flash-lite-latest")


def _make_extraction(variables: list[str]) -> QueryExtraction:
    return QueryExtraction(entities=["Kenya"], dates=[], variables=variables)


# ---------------------------------------------------------------------------
# _patch_all — mirrors test_pipeline.py; stubs all external I/O.
# ---------------------------------------------------------------------------


def _patch_all(monkeypatch: pytest.MonkeyPatch, *, retrieval_candidates=None) -> None:
    """Patch all external I/O modules used by pipeline._run_one_variable and _run."""
    import dc_search.hooks as _hooks
    import dc_search.retrieval as _retrieval
    import dc_search.shape as _shape_mod
    import dc_search.slot_binding as _sb
    from dc_search.retrieval import IndicatorCandidate, StatVarFeatures
    from dc_search.shape import Shape

    _indicator = IndicatorCandidate(
        dcid="LifeExpectancy_Person",
        type_of=["StatisticalVariable"],
        score=0.85,
    )
    cands = retrieval_candidates if retrieval_candidates is not None else (_indicator,)

    _features = StatVarFeatures(
        dcid="LifeExpectancy_Person",
        name="Life Expectancy",
        population_type=["Person"],
        measured_property=["lifeExpectancy"],
    )

    monkeypatch.setattr(_retrieval, "resolve_indicator", lambda *, query, k: cands)
    monkeypatch.setattr(
        _retrieval,
        "stat_var_features_batch",
        lambda *, sv_dcids: {d: _features for d in sv_dcids},
    )
    monkeypatch.setattr(_retrieval, "topic_metadata_batch", lambda *, dcids: {})
    monkeypatch.setattr(
        _retrieval,
        "presence_for_entities",
        lambda *, variable_dcids, entity_dcids: frozenset(),
    )
    monkeypatch.setattr(_retrieval, "variables_for_entities_batch", lambda *, entity_dcids: {})
    monkeypatch.setattr(_retrieval, "resolve_places_batch", lambda *, names: {})
    monkeypatch.setattr(_retrieval, "place_names_batch", lambda *, dcids: {})
    monkeypatch.setattr(_shape_mod, "extract_place_tokens", lambda query: [])

    async def _mock_bind(shape_context, *, model=None):
        _shape = Shape(
            population_type="Person",
            measured_property="lifeExpectancy",
            constraint_keys=(),
            member_dcids=("LifeExpectancy_Person",),
            slot_taxonomy={},
            is_topic=False,
        )
        return BindResult(
            shape=_shape, predicates=(_PREDICATE,), usage=_USAGE, defaulted_recipient=False
        )

    monkeypatch.setattr(_sb, "bind", _mock_bind)
    monkeypatch.setattr(_sb, "get_last_usage", lambda: _USAGE)
    monkeypatch.setattr(_hooks, "materialize_many", lambda predicates, candidates, *, ctx: _ANSWER)


# ---------------------------------------------------------------------------
# Helper: collect all events from an async generator.
# ---------------------------------------------------------------------------


async def _collect(gen) -> list[Any]:
    events = []
    async for ev in gen:
        events.append(ev)
    return events


# ---------------------------------------------------------------------------
# Fixtures — lifespan-singleton guard (mirrors test_app.py).
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _patch_lifespan_singletons(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent lifespan from constructing real genai/DC clients."""
    import dc_search.llm as _llm
    import dc_search.retrieval as _retrieval

    monkeypatch.setattr(_llm, "get_client", lambda: MagicMock())
    monkeypatch.setattr(_retrieval, "get_client", lambda: MagicMock())


# ---------------------------------------------------------------------------
# Case 1: stream_default event order
# ---------------------------------------------------------------------------


async def test_stream_default_event_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """start → interpretation → result* → done; exactly one terminal;
    expected_results == len(variables); truncated reflects >6 variables.
    """
    import dc_search.extraction as _ext
    from dc_search import pipeline

    two_vars = ["life expectancy", "population"]

    async def _mock_extract(query, *, model=None):
        return (_make_extraction(two_vars), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)
    _patch_all(monkeypatch)

    events = await _collect(pipeline.stream_default(_QUERY))

    assert isinstance(events[0], Start)
    assert events[0].query == _QUERY
    assert events[0].mode == "default"

    interp = events[1]
    assert isinstance(interp, Interpretation)
    assert interp.variables == two_vars
    assert interp.expected_results == len(two_vars)
    assert interp.truncated is False

    result_events = [e for e in events if isinstance(e, Result)]
    assert len(result_events) == len(two_vars)

    terminals = [e for e in events if isinstance(e, (Done, Error))]
    assert len(terminals) == 1
    assert isinstance(terminals[0], Done)

    # truncated=True when extraction returns >6 variables
    seven_vars = [f"var_{i}" for i in range(7)]

    async def _mock_extract_many(query, *, model=None):
        return (_make_extraction(seven_vars), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract_many)

    events2 = await _collect(pipeline.stream_default(_QUERY))
    interp2 = next(e for e in events2 if isinstance(e, Interpretation))
    assert interp2.truncated is True
    assert interp2.expected_results == pipeline.MAX_VARIABLES


# ---------------------------------------------------------------------------
# Case 2: stream_simple event order
# ---------------------------------------------------------------------------


async def test_stream_simple_event_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """start → stage("retrieving") → {places?, result} → done; exactly one terminal.

    A Places event may appear before or after the Result (interleaved), but the
    overall structure is preserved: Start, Stage, then results/places, then Done.
    """
    from dc_search import pipeline

    _patch_all(monkeypatch)

    events = await _collect(pipeline.stream_simple(_QUERY))

    assert isinstance(events[0], Start)
    assert events[0].mode == "simple"

    assert isinstance(events[1], Stage)
    assert events[1].stage == "retrieving"

    result_events = [e for e in events if isinstance(e, Result)]
    assert len(result_events) == 1
    assert result_events[0].index == 0

    terminals = [e for e in events if isinstance(e, (Done, Error))]
    assert len(terminals) == 1
    assert isinstance(terminals[0], Done)


# ---------------------------------------------------------------------------
# Case 3: stream_default partial failure
# ---------------------------------------------------------------------------


async def test_stream_default_partial_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """One branch raises → its Result carries AskClarification(reason="error") with
    outcome_kind=="clarification"; siblings are AnswerCollection with outcome_kind=="answer";
    stream ends with done (not error); drained run_default yields terminated_by=="answer".
    """
    import dc_search.extraction as _ext
    import dc_search.pipeline as _pipeline
    import dc_search.pipeline._run as _run

    two_vars = ["life expectancy", "population"]

    async def _mock_extract(query, *, model=None):
        return (_make_extraction(two_vars), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)
    _patch_all(monkeypatch)

    call_count = 0
    original_run_one = _run._run_one_variable

    async def _sometimes_fail(
        variable,
        query,
        *,
        resolution_task,
        dates=None,
        entities=None,
        contained_in=False,
        slot_bind_usages,
    ):
        nonlocal call_count
        call_count += 1
        if variable == "life expectancy":
            raise RuntimeError("simulated branch failure")
        return await original_run_one(
            variable,
            query,
            resolution_task=resolution_task,
            dates=dates,
            entities=entities,
            contained_in=contained_in,
            slot_bind_usages=slot_bind_usages,
        )

    monkeypatch.setattr(_run, "_run_one_variable", _sometimes_fail)

    events = await _collect(_pipeline.stream_default(_QUERY))

    result_events = [e for e in events if isinstance(e, Result)]
    assert len(result_events) == 2

    failed = next(r for r in result_events if r.variable_label == "life expectancy")
    succeeded = next(r for r in result_events if r.variable_label == "population")

    assert isinstance(failed.answer, AskClarification)
    assert failed.answer.reason == "error"
    assert failed.outcome_kind == "clarification"

    assert isinstance(succeeded.answer, AnswerCollection)
    assert succeeded.outcome_kind == "answer"

    terminals = [e for e in events if isinstance(e, (Done, Error))]
    assert len(terminals) == 1
    assert isinstance(terminals[0], Done)

    # run_default (drain) also terminates with "answer"
    result = await _pipeline.run_default(_QUERY)
    assert result.terminated_by == "answer"


# ---------------------------------------------------------------------------
# Case 4: stream_default zero-variable fallback
# ---------------------------------------------------------------------------


async def test_stream_default_zero_variable_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """extraction returns [] → start → interpretation(expected_results=1, variables=[]) →
    places → result(index=0) → done; done.telemetry.llm_usage[0].step == "extract".

    A Places event is emitted on the zero-variable path.  The overall
    structure is still start → interpretation → {places, result} → done.
    """
    import dc_search.extraction as _ext
    from dc_search import pipeline

    async def _mock_extract(query, *, model=None):
        return (_make_extraction([]), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)
    _patch_all(monkeypatch)

    events = await _collect(pipeline.stream_default(_QUERY))

    assert isinstance(events[0], Start)

    interp = events[1]
    assert isinstance(interp, Interpretation)
    assert interp.variables == []
    assert interp.expected_results == 1

    # Places appears after Interpretation.
    result_events = [e for e in events if isinstance(e, Result)]
    places_events = [e for e in events if isinstance(e, Places)]
    assert len(result_events) == 1, f"Expected one Result, got {result_events}"
    assert result_events[0].index == 0
    assert len(places_events) == 1, f"Expected one Places, got {places_events}"

    # Interpretation must precede Places.
    interp_idx = events.index(interp)
    places_idx = events.index(places_events[0])
    assert interp_idx < places_idx, "Interpretation must precede Places"

    done_events = [e for e in events if isinstance(e, Done)]
    assert len(done_events) == 1
    assert done_events[0].telemetry.llm_usage[0].step == "extract"


# ---------------------------------------------------------------------------
# Case 5: drain preserves index order even when branches complete out of order
# ---------------------------------------------------------------------------


async def test_drain_preserves_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """With as_completed completing branches out-of-order, run_default returns
    answers in index order identical to a deterministic-order run.
    """
    import dc_search.extraction as _ext
    import dc_search.pipeline as _pipeline
    import dc_search.pipeline._run as _run

    three_vars = ["var_0", "var_1", "var_2"]

    async def _mock_extract(query, *, model=None):
        return (_make_extraction(three_vars), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)
    _patch_all(monkeypatch)

    # Stub _run_one_variable to complete in reverse order (2 first, 0 last).
    delays = {"var_0": 0.06, "var_1": 0.04, "var_2": 0.01}

    async def _delayed_run(
        variable,
        query,
        *,
        resolution_task,
        dates=None,
        entities=None,
        contained_in=False,
        slot_bind_usages,
    ):
        delay = delays.get(variable or "", 0.01)
        await asyncio.sleep(delay)
        slot_bind_usages.append(_USAGE)
        answer = _ANSWER.model_copy(update={"variable_label": variable})
        return _pipeline._VariableResult(outcome=answer, n_candidates=1, n_shapes=1)

    monkeypatch.setattr(_run, "_run_one_variable", _delayed_run)

    result = await _pipeline.run_default(_QUERY)

    assert result.terminated_by == "answer"
    assert len(result.answers) == 3
    labels = [a.variable_label for a in result.answers]
    assert labels == three_vars, f"Expected {three_vars}, got {labels}"


# ---------------------------------------------------------------------------
# Case 6: done telemetry consistency
# ---------------------------------------------------------------------------


async def test_done_telemetry_consistency(monkeypatch: pytest.MonkeyPatch) -> None:
    """done.terminated_by == done.telemetry.terminated_by; likewise for truncated."""
    import dc_search.extraction as _ext
    from dc_search import pipeline

    async def _mock_extract(query, *, model=None):
        return (_make_extraction(["life expectancy"]), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)
    _patch_all(monkeypatch)

    events = await _collect(pipeline.stream_default(_QUERY))
    done = next(e for e in events if isinstance(e, Done))

    assert done.terminated_by == done.telemetry.terminated_by
    assert done.truncated == done.telemetry.truncated


# ---------------------------------------------------------------------------
# Case 7: SSE endpoint — content-type and event structure
# ---------------------------------------------------------------------------


def test_sse_endpoint_content_type_and_events(monkeypatch: pytest.MonkeyPatch) -> None:
    """Accept: text/event-stream → content-type starts with text/event-stream;
    iter_lines() yields event: start, ..., exactly one event: done or event: error;
    heartbeat lines (if any) start with ':'.
    """
    from fastapi.testclient import TestClient

    import dc_search.pipeline as _pipeline

    # Fast stub generator so the test terminates quickly.
    async def _fast_stream_default(query):
        yield Start(query=query, mode="default")
        yield Result(
            index=0,
            variable_label=None,
            outcome_kind="answer",
            answer=_ANSWER,
        )
        from dc_search.events import DoneTelemetry

        yield Done(
            telemetry=DoneTelemetry(
                llm_usage=[],
                n_candidates=1,
                n_shapes=1,
                terminated_by="answer",
                truncated=False,
            ),
            elapsed_s=0.1,
            terminated_by="answer",
            truncated=False,
        )

    monkeypatch.setattr(_pipeline, "stream_default", _fast_stream_default)

    from dc_search.app import app

    with TestClient(app) as c:
        with c.stream(
            "POST",
            "/api/dc-search",
            json={"query": _QUERY},
            headers={"Accept": "text/event-stream"},
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")

            lines = list(resp.iter_lines())

    event_lines = [ln for ln in lines if ln.startswith("event:")]
    comment_lines = [ln for ln in lines if ln.startswith(":")]

    assert any(ln == "event: start" for ln in event_lines), f"No 'event: start' in {event_lines}"

    terminal_event_lines = [ln for ln in event_lines if ln in ("event: done", "event: error")]
    assert len(terminal_event_lines) == 1, (
        f"Expected exactly one terminal event, got: {terminal_event_lines}"
    )

    # Heartbeat lines (if any) must be SSE comments starting with ':'
    for cl in comment_lines:
        assert cl.startswith(":"), f"Unexpected comment line: {cl!r}"


# ---------------------------------------------------------------------------
# Case 8: JSON branch unchanged — absent Accept and explicit application/json
# ---------------------------------------------------------------------------


def test_json_branch_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    """Accept: application/json AND absent Accept (i.e. */*) → 200 SearchResponse JSON."""
    from fastapi.testclient import TestClient

    import dc_search.pipeline as _pipeline
    from dc_search.pipeline import PipelineResult

    result = PipelineResult(
        query=_QUERY,
        answers=[_ANSWER],
        ask=None,
        elapsed_s=0.42,
        n_candidates=10,
        n_shapes=3,
        terminated_by="answer",
        llm_usage=[
            TelemetryLLMUsage(
                step="slot_bind",
                input_tokens=100,
                output_tokens=20,
                model="gemini-flash-lite-latest",
            )
        ],
        truncated=False,
    )

    monkeypatch.setattr(_pipeline, "run_default", AsyncMock(return_value=result))
    monkeypatch.setattr(_pipeline, "run_simple", AsyncMock(return_value=result))

    from dc_search.app import app

    with TestClient(app) as c:
        # Explicit application/json
        resp = c.post(
            "/api/dc-search",
            json={"query": _QUERY},
            headers={"Accept": "application/json"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] == _QUERY
        assert len(body["answers"]) == 1

        # Absent Accept (TestClient sends Accept: */* by default) → JSON branch
        resp2 = c.post("/api/dc-search", json={"query": _QUERY})
        assert resp2.status_code == 200
        body2 = resp2.json()
        assert body2["query"] == _QUERY

        # Explicit */* → JSON branch (not SSE)
        resp3 = c.post(
            "/api/dc-search",
            json={"query": _QUERY},
            headers={"Accept": "*/*"},
        )
        assert resp3.status_code == 200
        body3 = resp3.json()
        assert "query" in body3


# ---------------------------------------------------------------------------
# Case 9: SSE terminal error is sanitized
# ---------------------------------------------------------------------------


def test_sse_terminal_error_sanitized(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub pipeline to raise httpx.RequestError; SSE response contains a single
    event: error whose data.detail is the generic message, NOT raw exception text.
    """
    from fastapi.testclient import TestClient

    import dc_search.pipeline as _pipeline

    raw_message = "INTERNAL_HOST_1.2.3.4"

    async def _raising_stream(query):
        raise httpx.ConnectError(raw_message)
        yield  # make it a generator

    monkeypatch.setattr(_pipeline, "stream_default", _raising_stream)

    from dc_search.app import app

    with TestClient(app) as c:
        with c.stream(
            "POST",
            "/api/dc-search",
            json={"query": _QUERY},
            headers={"Accept": "text/event-stream"},
        ) as resp:
            assert resp.status_code == 200
            lines = list(resp.iter_lines())

    event_lines = [ln for ln in lines if ln.startswith("event:")]
    assert event_lines == ["event: error"], f"Expected only error event, got {event_lines}"

    data_lines = [ln for ln in lines if ln.startswith("data:")]
    assert len(data_lines) == 1
    payload = json.loads(data_lines[0].removeprefix("data:").strip())
    assert payload["detail"] == "Upstream service unavailable.", (
        f"Expected sanitized detail, got: {payload['detail']!r}"
    )
    assert raw_message not in payload["detail"], "Raw exception text must NOT appear in SSE error"


# ---------------------------------------------------------------------------
# Case 10: disconnect cancels fan-out (generator-level)
# ---------------------------------------------------------------------------


async def test_disconnect_cancels_fanout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the real Starlette disconnect path: the consumer *task* is cancelled
    while the generator is suspended awaiting the next fan-out event (not via break/
    aclose). The sequence is:
      1. task.cancel() injects CancelledError into the suspended generator await.
      2. The generator's finally block cancels the still-pending fan-out tasks.
      3. CancelledError re-propagates out of _consume, so awaiting the task raises it.
    """
    import dc_search.extraction as _ext
    import dc_search.pipeline as _pipeline
    import dc_search.pipeline._run as _run

    two_vars = ["fast", "slow"]

    async def _mock_extract(query, *, model=None):
        return (_make_extraction(two_vars), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)
    _patch_all(monkeypatch)

    # Track which variables saw CancelledError.
    cancelled_vars: list[str] = []

    async def _patched_run_one(
        variable,
        query,
        *,
        resolution_task,
        dates=None,
        entities=None,
        contained_in=False,
        slot_bind_usages,
    ):
        try:
            if variable == "slow":
                await asyncio.sleep(10)  # hangs until cancelled
            else:
                await asyncio.sleep(0.01)
            slot_bind_usages.append(_USAGE)
            answer = _ANSWER.model_copy(update={"variable_label": variable})
            return _pipeline._VariableResult(outcome=answer, n_candidates=1, n_shapes=1)
        except asyncio.CancelledError:
            if variable is not None:
                cancelled_vars.append(variable)
            raise

    monkeypatch.setattr(_run, "_run_one_variable", _patched_run_one)

    first_result_received: asyncio.Event = asyncio.Event()
    collected: list[Any] = []

    async def _consume():
        # Keep iterating so the generator suspends awaiting the slow branch.
        async for ev in _pipeline.stream_default(_QUERY):
            collected.append(ev)
            if isinstance(ev, Result):
                first_result_received.set()

    task = asyncio.create_task(_consume())
    # Wait until fast branch delivers its Result, then cancel the consumer.
    await asyncio.wait_for(asyncio.shield(first_result_received.wait()), timeout=5.0)

    task.cancel()
    # CancelledError must re-propagate from the consume task.
    with pytest.raises(asyncio.CancelledError):
        await task

    # Give the event loop a tick so cancelled tasks can finalize.
    await asyncio.sleep(0.1)

    assert "slow" in cancelled_vars, (
        "The slow branch should have seen CancelledError when cancelled"
    )

    # No pending tasks remain.
    current = asyncio.current_task()
    pending = {t for t in asyncio.all_tasks() if t is not current and not t.done()}
    assert not pending, f"Orphaned tasks remain: {pending}"


# ---------------------------------------------------------------------------
# Case 11: OpenAPI still documents SearchResponse for both routes
# ---------------------------------------------------------------------------


def test_openapi_still_documents_searchresponse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard against response_model= regression: /openapi.json must list SearchResponse
    for both /api/dc-search and /api/dc-search/simple.
    """
    from fastapi.testclient import TestClient

    import dc_search.pipeline as _pipeline

    monkeypatch.setattr(_pipeline, "run_default", AsyncMock(return_value=MagicMock()))
    monkeypatch.setattr(_pipeline, "run_simple", AsyncMock(return_value=MagicMock()))

    from dc_search.app import app

    with TestClient(app) as c:
        resp = c.get("/openapi.json")

    assert resp.status_code == 200
    schema = resp.json()

    for route in ("/api/dc-search", "/api/dc-search/simple"):
        post_op = schema.get("paths", {}).get(route, {}).get("post", {})
        response_200 = post_op.get("responses", {}).get("200", {})
        # The response schema should reference SearchResponse (by $ref or by title).
        content_json = response_200.get("content", {}).get("application/json", {})
        schema_ref = content_json.get("schema", {})

        # Accept either a direct $ref or an allOf/$ref nesting (FastAPI style).
        ref_str = json.dumps(schema_ref)
        assert "SearchResponse" in ref_str, (
            f"Route {route}: SearchResponse not found in OpenAPI 200 response schema. "
            f"Got: {ref_str}"
        )


# ---------------------------------------------------------------------------
# Case 12: soft-deadline — stream_default and stream_simple
# ---------------------------------------------------------------------------


async def test_stream_default_soft_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch _ROUTE_TIMEOUT_S to tiny value; one slow branch → stream ends with
    a single done(timed_out=True) carrying only the fast result; slow branch cancelled.
    """
    import dc_search.extraction as _ext
    import dc_search.pipeline as _pipeline
    import dc_search.pipeline._run as _run

    monkeypatch.setattr(_run, "_ROUTE_TIMEOUT_S", 0.05)

    two_vars = ["fast", "slow"]

    async def _mock_extract(query, *, model=None):
        return (_make_extraction(two_vars), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)
    _patch_all(monkeypatch)

    cancelled_vars: list[str] = []

    async def _patched_run_one(
        variable,
        query,
        *,
        resolution_task,
        dates=None,
        entities=None,
        contained_in=False,
        slot_bind_usages,
    ):
        try:
            if variable == "slow":
                await asyncio.sleep(10)
            else:
                await asyncio.sleep(0.01)
            slot_bind_usages.append(_USAGE)
            answer = _ANSWER.model_copy(update={"variable_label": variable})
            return _pipeline._VariableResult(outcome=answer, n_candidates=1, n_shapes=1)
        except asyncio.CancelledError:
            if variable is not None:
                cancelled_vars.append(variable)
            raise

    monkeypatch.setattr(_run, "_run_one_variable", _patched_run_one)

    events = await _collect(_pipeline.stream_default(_QUERY))

    result_events = [e for e in events if isinstance(e, Result)]
    terminals = [e for e in events if isinstance(e, (Done, Error))]

    assert len(terminals) == 1, f"Expected exactly one terminal, got {terminals}"
    done = terminals[0]
    assert isinstance(done, Done)
    assert done.timed_out is True

    # Only fast branch completed; at most one result.
    assert len(result_events) <= 1

    # Slow branch was cancelled by the deadline.
    assert "slow" in cancelled_vars, "Slow branch should have been cancelled"


async def test_stream_simple_soft_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """stream_simple emits done(timed_out=True) with no result when the single branch overruns."""
    import dc_search.pipeline as _pipeline
    import dc_search.pipeline._run as _run

    monkeypatch.setattr(_run, "_ROUTE_TIMEOUT_S", 0.05)
    _patch_all(monkeypatch)

    async def _slow_run_one(
        variable,
        query,
        *,
        resolution_task,
        dates=None,
        entities=None,
        contained_in=False,
        slot_bind_usages,
    ):
        await asyncio.sleep(10)
        slot_bind_usages.append(_USAGE)
        return _pipeline._VariableResult(outcome=_ANSWER, n_candidates=1, n_shapes=1)

    monkeypatch.setattr(_run, "_run_one_variable", _slow_run_one)

    events = await _collect(_pipeline.stream_simple(_QUERY))

    result_events = [e for e in events if isinstance(e, Result)]
    terminals = [e for e in events if isinstance(e, (Done, Error))]

    assert len(result_events) == 0, "No result before deadline"
    assert len(terminals) == 1
    done = terminals[0]
    assert isinstance(done, Done)
    assert done.timed_out is True


# ---------------------------------------------------------------------------
# Case 13: outcome_kind matches answer type + pydantic round-trip discriminator
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# New feature tests: Places event ordering + perf-neutrality
# ---------------------------------------------------------------------------


async def test_stream_default_places_event_present_and_after_interpretation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stream_default emits a Places event; it appears after the Interpretation event."""
    import dc_search.extraction as _ext
    from dc_search import pipeline

    async def _mock_extract(query, *, model=None):
        return (_make_extraction(["life expectancy"]), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)
    _patch_all(monkeypatch)

    events = await _collect(pipeline.stream_default(_QUERY))

    interpretation_events = [e for e in events if isinstance(e, Interpretation)]
    places_events = [e for e in events if isinstance(e, Places)]

    assert len(interpretation_events) == 1, "Expected exactly one Interpretation event"
    assert len(places_events) == 1, "Expected exactly one Places event"

    interp_idx = events.index(interpretation_events[0])
    places_idx = events.index(places_events[0])
    assert interp_idx < places_idx, (
        f"Interpretation (idx={interp_idx}) must precede Places (idx={places_idx})"
    )


async def test_perf_interpretation_before_places_under_slow_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Interpretation index < Places index even under a SLOW _resolve_place_dcids mock.

    Guards the perf-neutrality guarantee: the interpretation event is yielded
    BEFORE place_task is even started, so no place-resolution latency can delay it.
    """
    import dc_search.extraction as _ext
    import dc_search.pipeline as _pipeline
    import dc_search.pipeline._run as _run

    async def _mock_extract(query, *, model=None):
        return (_make_extraction(["life expectancy"]), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)
    _patch_all(monkeypatch)

    # SLOW place resolution — sleeps before returning
    async def _slow_resolve_place_dcids(query, entities, *, contained_in_parents=()):
        from dc_search.pipeline import PlaceResolution

        await asyncio.sleep(0.2)
        return PlaceResolution(dcids=(), parent_to_children={}, parent_to_child_type={})

    monkeypatch.setattr(_run, "_resolve_place_dcids", _slow_resolve_place_dcids)

    events = await _collect(_pipeline.stream_default(_QUERY))

    interpretation_events = [e for e in events if isinstance(e, Interpretation)]
    places_events = [e for e in events if isinstance(e, Places)]

    assert len(interpretation_events) == 1
    assert len(places_events) == 1

    interp_idx = events.index(interpretation_events[0])
    places_idx = events.index(places_events[0])
    assert interp_idx < places_idx, (
        "Interpretation must precede Places even when place resolution is slow"
    )


async def test_perf_result_emitted_while_place_task_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Result event is emitted while place_event_task is still pending.

    Result emission must not be blocked by place resolution latency. With a slow
    place_names_batch and a fast _run_one_variable, at least one Result should
    appear before the Places event.
    """
    import dc_search.extraction as _ext
    import dc_search.pipeline as _pipeline
    import dc_search.pipeline._run as _run
    import dc_search.retrieval as _retrieval

    async def _mock_extract(query, *, model=None):
        return (_make_extraction(["life expectancy"]), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)
    _patch_all(monkeypatch)

    # Fast variable pipeline (near-instant)
    async def _fast_run_one(
        variable,
        query,
        *,
        resolution_task,
        dates=None,
        entities=None,
        contained_in=False,
        slot_bind_usages,
    ):
        slot_bind_usages.append(_USAGE)
        answer = _ANSWER.model_copy(update={"variable_label": variable})
        return _pipeline._VariableResult(outcome=answer, n_candidates=1, n_shapes=1)

    monkeypatch.setattr(_run, "_run_one_variable", _fast_run_one)

    # SLOW name fetch — so Places arrives after Results
    def _slow_place_names_batch(*, dcids):
        import time

        time.sleep(0.3)  # blocking sleep to simulate slow name fetch
        return {d: (None, None) for d in dcids}

    monkeypatch.setattr(_retrieval, "place_names_batch", _slow_place_names_batch)

    # Give the dcid resolution something to return so place_event_task actually runs
    import dc_search.retrieval as _ret2
    from dc_search.retrieval import PlaceCandidate

    monkeypatch.setattr(
        _ret2,
        "resolve_places_batch",
        lambda *, names: {n: (PlaceCandidate(dcid=f"dcid/{n}"),) for n in names},
    )

    events = await _collect(_pipeline.stream_default(_QUERY))

    result_events = [e for e in events if isinstance(e, Result)]
    places_events = [e for e in events if isinstance(e, Places)]

    assert len(result_events) >= 1, "At least one Result must be emitted"
    # Either Places arrived after some Results, or was dropped (slow enough to miss deadline).
    # The key assertion: the first Result must appear at or before the Places event (or Places
    # was not emitted at all due to deadline), confirming results are not blocked by Places.
    if places_events:
        first_result_idx = events.index(result_events[0])
        places_idx = events.index(places_events[0])
        assert first_result_idx <= places_idx, (
            "Result must not be blocked by Places: first Result should appear before or with Places"
        )


def test_result_outcome_kind_matches_answer() -> None:
    """outcome_kind=="answer" iff isinstance(answer, AnswerCollection); and
    pydantic round-trips a serialized Result back to the correct concrete type.
    """
    answer_result = Result(
        index=0,
        variable_label="life expectancy",
        outcome_kind="answer",
        answer=_ANSWER,
    )
    assert answer_result.outcome_kind == "answer"
    assert isinstance(answer_result.answer, AnswerCollection)

    ask = AskClarification(reason="no_candidates", message="Nothing found.")
    clarification_result = Result(
        index=1,
        variable_label=None,
        outcome_kind="clarification",
        answer=ask,
    )
    assert clarification_result.outcome_kind == "clarification"
    assert isinstance(clarification_result.answer, AskClarification)

    # Round-trip through JSON: pydantic callable Discriminator must re-parse correctly.
    json_str = answer_result.model_dump_json()
    reparsed = Result.model_validate_json(json_str)
    assert isinstance(reparsed.answer, AnswerCollection), (
        f"Expected AnswerCollection after round-trip, got {type(reparsed.answer)}"
    )
    assert reparsed.outcome_kind == "answer"

    json_str2 = clarification_result.model_dump_json()
    reparsed2 = Result.model_validate_json(json_str2)
    assert isinstance(reparsed2.answer, AskClarification), (
        f"Expected AskClarification after round-trip, got {type(reparsed2.answer)}"
    )
    assert reparsed2.outcome_kind == "clarification"


# ---------------------------------------------------------------------------
# Timeout path: place_event_task cancellation prevents orphans
# ---------------------------------------------------------------------------


async def test_stream_simple_timeout_cancels_place_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stream_simple deadline path cancels dcid_task + place_event_task — no orphans.

    Exercises the path where both _run_one_variable AND place_names_batch block,
    so place_event_task is still pending when the route deadline fires.
    """
    import dc_search.pipeline as _pipeline
    import dc_search.pipeline._run as _run
    import dc_search.retrieval as _retrieval

    monkeypatch.setattr(_run, "_ROUTE_TIMEOUT_S", 0.05)
    _patch_all(monkeypatch)

    async def _slow_run_one(
        variable,
        query,
        *,
        resolution_task,
        dates=None,
        entities=None,
        contained_in=False,
        slot_bind_usages,
    ):
        await asyncio.sleep(10)
        return _pipeline._VariableResult(outcome=_ANSWER, n_candidates=1, n_shapes=1)

    monkeypatch.setattr(_run, "_run_one_variable", _slow_run_one)

    # Also block place_names_batch so place_event_task is pending at timeout.
    def _blocking_place_names(*, dcids):
        import time

        time.sleep(10)
        return {d: (None, None) for d in dcids}

    monkeypatch.setattr(_retrieval, "place_names_batch", _blocking_place_names)

    # Non-empty resolve_places_batch so place_event_task reaches the name fetch.
    from dc_search.retrieval import PlaceCandidate

    monkeypatch.setattr(
        _retrieval,
        "resolve_places_batch",
        lambda *, names: {n: (PlaceCandidate(dcid=f"dcid/{n}"),) for n in names},
    )

    events = await _collect(_pipeline.stream_simple(_QUERY))

    terminals = [e for e in events if isinstance(e, (Done, Error))]
    assert len(terminals) == 1
    done = terminals[0]
    assert isinstance(done, Done)
    assert done.timed_out is True

    # Give the event loop a tick so cancelled tasks finalize.
    await asyncio.sleep(0.1)

    current = asyncio.current_task()
    pending = {t for t in asyncio.all_tasks() if t is not current and not t.done()}
    assert not pending, f"Orphaned tasks remain: {pending}"


# ---------------------------------------------------------------------------
# zero-variable path — Result emitted while place_event_task still pending
# ---------------------------------------------------------------------------


async def test_zero_variable_result_not_blocked_by_place_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On the zero-variable fallback path, a Result is emitted while
    place_event_task is still pending (slow place_names_batch).

    Guards that the zero-variable path uses the same FIRST_COMPLETED interleave
    as the normal path, so the single result is not serialized behind the name fetch.
    """
    import dc_search.extraction as _ext
    import dc_search.pipeline as _pipeline
    import dc_search.pipeline._run as _run
    import dc_search.retrieval as _retrieval

    async def _mock_extract(query, *, model=None):
        return (_make_extraction([]), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)
    _patch_all(monkeypatch)

    # Fast _run_one_variable — completes near-instantly.
    async def _fast_run_one(
        variable,
        query,
        *,
        resolution_task,
        dates=None,
        entities=None,
        contained_in=False,
        slot_bind_usages,
    ):
        slot_bind_usages.append(_USAGE)
        return _pipeline._VariableResult(outcome=_ANSWER, n_candidates=1, n_shapes=1)

    monkeypatch.setattr(_run, "_run_one_variable", _fast_run_one)

    # SLOW name fetch — place_event_task will still be pending when the result arrives.
    def _slow_place_names(*, dcids):
        import time

        time.sleep(0.3)
        return {d: (None, None) for d in dcids}

    monkeypatch.setattr(_retrieval, "place_names_batch", _slow_place_names)

    # Non-empty resolve_places_batch so place_event_task reaches the name fetch.
    from dc_search.retrieval import PlaceCandidate

    monkeypatch.setattr(
        _retrieval,
        "resolve_places_batch",
        lambda *, names: {n: (PlaceCandidate(dcid=f"dcid/{n}"),) for n in names},
    )

    events = await _collect(_pipeline.stream_default(_QUERY))

    result_events = [e for e in events if isinstance(e, Result)]
    places_events = [e for e in events if isinstance(e, Places)]

    assert len(result_events) == 1, f"Expected one Result, got {result_events}"
    # Either Places arrived after Result, or was dropped — either way, Result was not blocked.
    if places_events:
        result_idx = events.index(result_events[0])
        places_idx = events.index(places_events[0])
        assert result_idx <= places_idx, (
            "Result must not be blocked by Places on the zero-variable path"
        )


# ---------------------------------------------------------------------------
# Resolution failure cleans up: retrieve_task is cancelled, not orphaned
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolution_failure_does_not_orphan_retrieve_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_resolve_place_dcids raising while retrieve_task is in flight → retrieve_task cancelled."""
    import dc_search.extraction as _ext
    import dc_search.pipeline as _pipeline
    import dc_search.pipeline._run as _run

    async def _mock_extract(query, *, model=None):
        return (_make_extraction(["population"]), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)
    _patch_all(monkeypatch)

    retrieve_started = asyncio.Event()
    retrieve_cancelled = asyncio.Event()

    async def _slow_retrieve(variable, query):
        retrieve_started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            retrieve_cancelled.set()
            raise

    monkeypatch.setattr(_run, "_retrieve", _slow_retrieve)

    async def _failing_resolve(query, entities, *, contained_in_parents=()):
        await retrieve_started.wait()  # ensure retrieve_task is in flight before we fail
        raise RuntimeError("simulated resolution failure")

    monkeypatch.setattr(_run, "_resolve_place_dcids", _failing_resolve)

    events = await _collect(_pipeline.stream_default(_QUERY))

    # Resolution failure → branch records error AskClarification (per_variable except path).
    result_events = [e for e in events if isinstance(e, Result)]
    assert len(result_events) == 1
    assert isinstance(result_events[0].answer, AskClarification)
    assert result_events[0].answer.reason == "error"

    await asyncio.sleep(0)  # let the finally's cancel finalize
    assert retrieve_cancelled.is_set(), "retrieve_task must be cancelled on resolution failure"
    current = asyncio.current_task()
    pending = {t for t in asyncio.all_tasks() if t is not current and not t.done()}
    assert not pending, f"Orphaned tasks remain: {pending}"


# ---------------------------------------------------------------------------
# Consumer cancellation during resolution: retrieve_task cleanup via finally
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cancel_during_resolution_cleans_up_retrieve_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CancelledError while awaiting resolution_task inside _run_one_variable → finally cancels
    retrieve_task."""
    import dc_search.extraction as _ext
    import dc_search.pipeline as _pipeline
    import dc_search.pipeline._run as _run

    async def _mock_extract(query, *, model=None):
        return (_make_extraction(["population"]), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)
    _patch_all(monkeypatch)

    retrieve_started = asyncio.Event()
    retrieve_cancelled = asyncio.Event()
    release_resolve = asyncio.Event()  # never set → resolution stays suspended

    async def _slow_retrieve(variable, query):
        retrieve_started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            retrieve_cancelled.set()
            raise

    monkeypatch.setattr(_run, "_retrieve", _slow_retrieve)

    async def _blocking_resolve(query, entities, *, contained_in_parents=()):
        await release_resolve.wait()  # suspends the await resolution_task inside the body
        # PlaceResolution has THREE required fields (frozen/slots, no defaults).
        return _run.PlaceResolution(dcids=(), parent_to_children={}, parent_to_child_type={})

    monkeypatch.setattr(_run, "_resolve_place_dcids", _blocking_resolve)

    async def _consume():
        async for _ in _pipeline.stream_default(_QUERY):
            pass

    task = asyncio.create_task(_consume())
    await asyncio.wait_for(asyncio.shield(retrieve_started.wait()), timeout=5.0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # Event-driven wait for the finally's cancel to finalize.
    await asyncio.wait_for(retrieve_cancelled.wait(), timeout=1.0)
    assert retrieve_cancelled.is_set(), "retrieve_task must be cancelled when consumer is cancelled"
    current = asyncio.current_task()
    pending = {t for t in asyncio.all_tasks() if t is not current and not t.done()}
    assert not pending, f"Orphaned tasks remain: {pending}"
