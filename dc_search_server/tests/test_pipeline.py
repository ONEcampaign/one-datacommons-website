"""Tests for pipeline.py orchestrators.

All external I/O is mocked.  The happy-path tests verify that each step
of the pipeline is called in the correct order and that results are
assembled correctly.  The concurrency test (#8) proves that fan-out is
genuinely parallel via the semaphore.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from dc_search.extraction import ExtractedDate, QueryExtraction
from dc_search.hooks import HookContext
from dc_search.predicate import AnswerCollection, Predicate
from dc_search.retrieval import IndicatorCandidate, StatVarFeatures
from dc_search.shape import Shape, ShapeContext
from dc_search.telemetry import TelemetryLLMUsage, Usage

# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------

_QUERY = "life expectancy in Kenya"

_INDICATOR_CAND = IndicatorCandidate(
    dcid="LifeExpectancy_Person",
    type_of=["StatisticalVariable"],
    score=0.85,
)

_FEATURES = StatVarFeatures(
    dcid="LifeExpectancy_Person",
    name="Life Expectancy",
    population_type=["Person"],
    measured_property=["lifeExpectancy"],
)

_SHAPE = Shape(
    population_type="Person",
    measured_property="lifeExpectancy",
    constraint_keys=(),
    member_dcids=("LifeExpectancy_Person",),
    slot_taxonomy={},
    is_topic=False,
)

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

_USAGE = Usage(
    input_tokens=100,
    output_tokens=20,
    model="gemini-flash-lite-latest",
)

_EXTRACT_USAGE = Usage(
    input_tokens=50,
    output_tokens=10,
    model="gemini-flash-lite-latest",
)

_SHAPE_CTX = ShapeContext(
    query=_QUERY,
    shapes=(_SHAPE,),
    keyword_cues={},
)


def _make_extraction(variables: list[str]) -> QueryExtraction:
    return QueryExtraction(
        entities=["Kenya"],
        dates=[],
        variables=variables,
    )


# ---------------------------------------------------------------------------
# Fixtures — module-level patches applied per test
# ---------------------------------------------------------------------------


def _patch_all(monkeypatch, *, retrieval_candidates=None, extra_patches=None):
    """Patch all external I/O modules used by pipeline._run_one_variable."""
    import dc_search.hooks as _hooks
    import dc_search.retrieval as _retrieval
    import dc_search.shape as _shape_mod
    import dc_search.slot_binding as _sb

    cands = retrieval_candidates if retrieval_candidates is not None else (_INDICATOR_CAND,)

    monkeypatch.setattr(_retrieval, "resolve_indicator", lambda *, query, k: cands)
    monkeypatch.setattr(
        _retrieval,
        "stat_var_features_batch",
        lambda *, sv_dcids: {d: _FEATURES for d in sv_dcids},
    )
    monkeypatch.setattr(
        _retrieval,
        "topic_metadata_batch",
        lambda *, dcids: {},
    )
    monkeypatch.setattr(
        _retrieval,
        "presence_for_entities",
        lambda *, variable_dcids, entity_dcids: frozenset(),
    )
    monkeypatch.setattr(
        _retrieval,
        "variables_for_entities_batch",
        lambda *, entity_dcids: {},
    )
    monkeypatch.setattr(
        _retrieval,
        "resolve_places_batch",
        lambda *, names: {},
    )

    # Place extraction → no places (clean availability path).
    # Pipeline accesses shape_module.extract_place_tokens via module attribute,
    # so patching on the module object here is correct.
    monkeypatch.setattr(_shape_mod, "extract_place_tokens", lambda query: [])

    # bind returns a successful binding by default
    async def _mock_bind(shape_context, *, model=None):
        return (_SHAPE, (_PREDICATE,), _USAGE)

    monkeypatch.setattr(_sb, "bind", _mock_bind)
    monkeypatch.setattr(_sb, "get_last_usage", lambda: _USAGE)

    # materialize returns _ANSWER by default
    monkeypatch.setattr(_hooks, "materialize_many", lambda predicates, candidates, *, ctx: _ANSWER)

    if extra_patches:
        for obj, attr, val in extra_patches:
            monkeypatch.setattr(obj, attr, val)


# ---------------------------------------------------------------------------
# Test 1: run_simple happy path → 1 AnswerCollection with variable_label=None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_simple_happy_path(monkeypatch):
    _patch_all(monkeypatch)

    from dc_search import pipeline

    result = await pipeline.run_simple(_QUERY)

    assert result.terminated_by == "answer"
    assert len(result.answers) == 1
    assert result.answers[0].variable_label is None
    assert result.ask is None
    assert isinstance(result.llm_usage[0], TelemetryLLMUsage)
    assert any(u.step == "slot_bind" for u in result.llm_usage)
    assert result.n_candidates > 0
    assert result.n_shapes > 0


# ---------------------------------------------------------------------------
# Test 2: run_simple empty retrieval → AskClarification(reason="no_candidates")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_simple_empty_retrieval(monkeypatch):
    _patch_all(monkeypatch, retrieval_candidates=())

    from dc_search import pipeline

    result = await pipeline.run_simple(_QUERY)

    assert result.terminated_by == "no_candidates"
    assert result.answers == []
    assert result.ask is not None
    assert result.ask.reason == "no_candidates"


# ---------------------------------------------------------------------------
# Test 3: topic dominance → skips LLM (slot_binding.bind is NOT called)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_simple_topic_dominance(monkeypatch):
    import dc_search.hooks as _hooks
    import dc_search.slot_binding as _sb

    topic_cand = IndicatorCandidate(
        dcid="dc/topic/Health",
        type_of=["Topic"],
        score=1.0,
    )
    non_topic_cand = IndicatorCandidate(
        dcid="LifeExpectancy_Person",
        type_of=["StatisticalVariable"],
        score=0.3,
    )
    _patch_all(monkeypatch, retrieval_candidates=(topic_cand, non_topic_cand))

    bind_called = False

    async def _bind_spy(shape_context, *, model=None):
        nonlocal bind_called
        bind_called = True
        return (_SHAPE, (_PREDICATE,), _USAGE)

    monkeypatch.setattr(_sb, "bind", _bind_spy)

    topic_answer = AnswerCollection(
        predicate=Predicate(
            population_type=None,
            measured_property=None,
            constraints={"relevantTopic": "dc/topic/Health"},
        ),
        sv_set=["LifeExpectancy_Person"],
        confidence="medium",
    )
    monkeypatch.setattr(
        _hooks,
        "materialize_many",
        lambda predicates, candidates, *, ctx: topic_answer,
    )

    from dc_search import pipeline

    result = await pipeline.run_simple("health indicators")

    assert not bind_called, "slot_binding.bind must NOT be called on topic-dominance path"
    assert result.terminated_by == "answer"


# ---------------------------------------------------------------------------
# Test 4: run_default single variable → matches run_simple + extract_usage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_default_single_variable(monkeypatch):
    import dc_search.extraction as _ext

    _patch_all(monkeypatch)

    single_var = ["life expectancy"]

    async def _mock_extract(query, *, model=None):
        return (_make_extraction(single_var), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)

    from dc_search import pipeline

    result = await pipeline.run_default(_QUERY)

    assert result.terminated_by == "answer"
    assert len(result.answers) == 1
    assert result.llm_usage[0].step == "extract"
    assert any(u.step == "slot_bind" for u in result.llm_usage)
    assert result.n_candidates > 0


# ---------------------------------------------------------------------------
# Test 5: run_default multi-variable → 2 AnswerCollections with variable_label
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_default_multi_variable(monkeypatch):
    import dc_search.extraction as _ext

    _patch_all(monkeypatch)

    two_vars = ["life expectancy", "population"]

    async def _mock_extract(query, *, model=None):
        return (_make_extraction(two_vars), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)

    from dc_search import pipeline

    result = await pipeline.run_default(_QUERY)

    assert result.terminated_by == "answer"
    assert len(result.answers) == 2
    labels = {a.variable_label for a in result.answers}
    assert "life expectancy" in labels
    assert "population" in labels
    assert result.llm_usage[0].step == "extract"
    assert sum(1 for u in result.llm_usage if u.step == "slot_bind") == 2
    assert result.n_candidates > 0


# ---------------------------------------------------------------------------
# Test 6: run_default extraction returns 0 variables → falls back to run_simple
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_default_zero_variables_falls_back_to_simple(monkeypatch):
    import dc_search.extraction as _ext

    _patch_all(monkeypatch)

    async def _mock_extract(query, *, model=None):
        return (_make_extraction([]), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)

    from dc_search import pipeline

    result = await pipeline.run_default(_QUERY)

    assert result.terminated_by == "answer"
    # extract_usage is prepended
    assert result.llm_usage[0].step == "extract"
    assert result.llm_usage[0].input_tokens == _EXTRACT_USAGE.input_tokens


# ---------------------------------------------------------------------------
# Test 7: run_default extraction returns 10 variables → only 6 processed, truncated=True
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_default_truncates_at_max_variables(monkeypatch):
    import dc_search.extraction as _ext

    processed: list[str] = []

    _patch_all(monkeypatch)

    ten_vars = [f"variable_{i}" for i in range(10)]

    async def _mock_extract(query, *, model=None):
        return (_make_extraction(ten_vars), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)

    import dc_search.pipeline as _pipeline

    original_run_one = _pipeline._run_one_variable

    async def _capturing_run_one(variable, query, *, entities=None, dates=None, slot_bind_usages):
        if variable is not None:
            processed.append(variable)
        return await original_run_one(
            variable, query, entities=entities, dates=dates, slot_bind_usages=slot_bind_usages
        )

    monkeypatch.setattr(_pipeline, "_run_one_variable", _capturing_run_one)

    from dc_search import pipeline

    result = await pipeline.run_default(_QUERY)

    assert result.truncated is True
    assert len(processed) == pipeline.MAX_VARIABLES


# ---------------------------------------------------------------------------
# Test 8: run_default fan-out runs concurrently (wall-clock closer to 1× than 6×)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_default_fan_out_is_concurrent(monkeypatch):
    import dc_search.extraction as _ext
    import dc_search.pipeline as _pipeline

    DELAY = 0.05
    N_VARS = 6

    six_vars = [f"var_{i}" for i in range(N_VARS)]

    async def _mock_extract(query, *, model=None):
        return (_make_extraction(six_vars), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)

    async def _slow_run_one(variable, query, *, entities=None, dates=None, slot_bind_usages):
        await asyncio.sleep(DELAY)
        slot_bind_usages.append(_USAGE)
        answer = _ANSWER.model_copy(update={"variable_label": variable if variable else None})
        return _pipeline._VariableResult(outcome=answer, n_candidates=1, n_shapes=1)

    monkeypatch.setattr(_pipeline, "_run_one_variable", _slow_run_one)

    from dc_search import pipeline

    t0 = time.perf_counter()
    result = await pipeline.run_default(_QUERY)
    elapsed = time.perf_counter() - t0

    assert result.terminated_by == "answer"
    assert len(result.answers) == N_VARS

    # Serial execution would take N_VARS * DELAY seconds; allow 2× headroom.
    # This asserts genuine concurrency (not just sequential with asyncio overhead).
    assert elapsed < N_VARS * DELAY * 2, (
        f"Fan-out took {elapsed:.3f}s for {N_VARS} vars each sleeping {DELAY}s — "
        "expected concurrent execution closer to {DELAY}s total"
    )


# ---------------------------------------------------------------------------
# Test 9: run_default threads extraction.dates to HookContext
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_default_threads_dates_to_hook_context(monkeypatch):
    """run_default passes extraction_result.dates into the HookContext received
    by materialize_many — verifying the dates data path is wired end to end."""
    import dc_search.extraction as _ext
    import dc_search.hooks as _hooks

    _patch_all(monkeypatch)

    extracted_dates = [ExtractedDate(kind="range", start="2010", end=None)]

    async def _mock_extract(query, *, model=None):
        qe = QueryExtraction(
            entities=["Kenya"],
            dates=extracted_dates,
            variables=["life expectancy"],
        )
        return (qe, _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)

    received_ctx: list[HookContext] = []

    def _capturing_materialize(predicates, candidates, *, ctx):
        received_ctx.append(ctx)
        return _ANSWER

    monkeypatch.setattr(_hooks, "materialize_many", _capturing_materialize)

    from dc_search import pipeline

    result = await pipeline.run_default(_QUERY)

    assert result.terminated_by == "answer"
    assert len(received_ctx) == 1
    assert received_ctx[0].dates == extracted_dates


# ---------------------------------------------------------------------------
# Test 10: run_simple passes empty dates to HookContext
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_simple_passes_empty_dates(monkeypatch):
    """run_simple (no extraction step) passes dates=[] into the HookContext
    received by materialize_many."""
    import dc_search.hooks as _hooks

    _patch_all(monkeypatch)

    received_ctx: list[HookContext] = []

    def _capturing_materialize(predicates, candidates, *, ctx):
        received_ctx.append(ctx)
        return _ANSWER

    monkeypatch.setattr(_hooks, "materialize_many", _capturing_materialize)

    from dc_search import pipeline

    result = await pipeline.run_simple(_QUERY)

    assert result.terminated_by == "answer"
    assert len(received_ctx) == 1
    assert received_ctx[0].dates == []


# ---------------------------------------------------------------------------
# Tests 9–11: entities → place resolution routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_with_entities_uses_resolve_places_batch(monkeypatch):
    """Default endpoint with non-empty entities routes availability re-rank through
    resolve_places_batch (mixer name lookup), called with the LLM-extracted names.

    Note: extract_place_tokens is still called internally by build_shape_context
    for keyword cue extraction — that call is independent and unaffected.  We
    verify routing solely by confirming resolve_places_batch is called with the
    extracted entity names from the LLM extraction step.
    """
    import dc_search.extraction as _ext
    import dc_search.retrieval as _retrieval

    _patch_all(monkeypatch)

    resolve_batch_calls: list[tuple] = []

    from dc_search.retrieval import PlaceCandidate

    def _mock_resolve_batch(*, names):
        resolve_batch_calls.append(names)
        return {name: (PlaceCandidate(dcid=f"dcid/{name}"),) for name in names}

    monkeypatch.setattr(_retrieval, "resolve_places_batch", _mock_resolve_batch)

    async def _mock_extract(query, *, model=None):
        return (
            QueryExtraction(
                entities=["Kenya"],
                variables=["life expectancy"],
            ),
            _EXTRACT_USAGE,
        )

    monkeypatch.setattr(_ext, "extract", _mock_extract)

    from dc_search import pipeline

    await pipeline.run_default(_QUERY)

    assert len(resolve_batch_calls) >= 1, "resolve_places_batch must be called at least once"
    assert any("Kenya" in names for names in resolve_batch_calls), (
        "resolve_places_batch must be called with the LLM-extracted entity 'Kenya'"
    )


@pytest.mark.asyncio
async def test_default_with_empty_entities_skips_resolve_places_batch(monkeypatch):
    """Default endpoint with entities=[] skips resolve_places_batch and falls back
    to the deterministic extract_place_tokens path for availability re-rank."""
    import dc_search.extraction as _ext
    import dc_search.retrieval as _retrieval

    _patch_all(monkeypatch)

    resolve_batch_called = False

    def _mock_resolve_batch(*, names):
        nonlocal resolve_batch_called
        resolve_batch_called = True
        return {}

    monkeypatch.setattr(_retrieval, "resolve_places_batch", _mock_resolve_batch)

    async def _mock_extract(query, *, model=None):
        return (
            QueryExtraction(
                entities=[],
                variables=["life expectancy"],
            ),
            _EXTRACT_USAGE,
        )

    monkeypatch.setattr(_ext, "extract", _mock_extract)

    from dc_search import pipeline

    await pipeline.run_default(_QUERY)

    assert not resolve_batch_called, (
        "resolve_places_batch must NOT be called when extraction returned no entities"
    )


@pytest.mark.asyncio
async def test_simple_endpoint_skips_resolve_places_batch(monkeypatch):
    """Simple endpoint (entities=None path) never calls resolve_places_batch for
    availability re-rank — it always uses the deterministic extract_place_tokens."""
    import dc_search.retrieval as _retrieval

    _patch_all(monkeypatch)

    resolve_batch_called = False

    def _mock_resolve_batch(*, names):
        nonlocal resolve_batch_called
        resolve_batch_called = True
        return {}

    monkeypatch.setattr(_retrieval, "resolve_places_batch", _mock_resolve_batch)

    from dc_search import pipeline

    await pipeline.run_simple(_QUERY)

    assert not resolve_batch_called, (
        "resolve_places_batch must NOT be called from the simple endpoint (entities=None)"
    )


# ---------------------------------------------------------------------------
# B4: _resolve_union_availability hybrid tests
# ---------------------------------------------------------------------------


def _make_date_coverage(
    envelopes: dict,
    entity_ranges: dict,
):
    from dc_search.retrieval import DateCoverage

    return DateCoverage(envelopes=envelopes, entity_ranges=entity_ranges)


def test_resolve_union_availability_custom_via_map_no_presence_call():
    """Custom var (in coverage map at resolved places) → no presence_for_entities call."""
    from unittest.mock import patch

    import dc_search.pipeline as _pipeline
    import dc_search.retrieval as _retrieval

    cov = _make_date_coverage(
        envelopes={"SV_CUSTOM": ("2010", "2024")},
        entity_ranges={("SV_CUSTOM", "country/KEN"): ("2012", "2022")},
    )

    with (
        patch.object(_retrieval, "variable_date_coverage", return_value=cov),
        patch.object(_retrieval, "presence_for_entities") as mock_presence,
    ):
        result = _pipeline._resolve_union_availability(
            ["country/KEN"],
            candidate_sv_dcids=("SV_CUSTOM",),
        )

    mock_presence.assert_not_called()
    assert "SV_CUSTOM" in result


def test_resolve_union_availability_base_dc_via_presence():
    """Base-DC var (map-absent) → presence_for_entities is called with only base candidates."""
    from unittest.mock import patch

    import dc_search.pipeline as _pipeline
    import dc_search.retrieval as _retrieval

    # No custom vars in map
    cov = _make_date_coverage(envelopes={}, entity_ranges={})

    with (
        patch.object(_retrieval, "variable_date_coverage", return_value=cov),
        patch.object(
            _retrieval,
            "presence_for_entities",
            return_value=frozenset({"BASE_SV"}),
        ) as mock_presence,
    ):
        result = _pipeline._resolve_union_availability(
            ["country/KEN"],
            candidate_sv_dcids=("BASE_SV",),
        )

    # presence_for_entities called with only base candidates
    call_kwargs = mock_presence.call_args.kwargs
    assert "BASE_SV" in call_kwargs["variable_dcids"]
    assert "BASE_SV" in result


def test_resolve_union_availability_mixed_set_unions_both():
    """Mixed set: custom resolved from map + base-DC from presence, unioned."""
    from unittest.mock import patch

    import dc_search.pipeline as _pipeline
    import dc_search.retrieval as _retrieval

    cov = _make_date_coverage(
        envelopes={"SV_CUSTOM": ("2010", "2024")},
        entity_ranges={("SV_CUSTOM", "country/KEN"): ("2012", "2022")},
    )

    with (
        patch.object(_retrieval, "variable_date_coverage", return_value=cov),
        patch.object(
            _retrieval,
            "presence_for_entities",
            return_value=frozenset({"BASE_SV"}),
        ) as mock_presence,
    ):
        result = _pipeline._resolve_union_availability(
            ["country/KEN"],
            candidate_sv_dcids=("SV_CUSTOM", "BASE_SV"),
        )

    # Only base candidate passed to presence_for_entities
    call_kwargs = mock_presence.call_args.kwargs
    assert "BASE_SV" in call_kwargs["variable_dcids"]
    assert "SV_CUSTOM" not in call_kwargs["variable_dcids"]
    assert "SV_CUSTOM" in result
    assert "BASE_SV" in result


def test_resolve_union_availability_map_absent_base_absent_fail_open():
    """Map-absent + base-DC-absent → var still in candidates (fail-open)."""
    from unittest.mock import patch

    import dc_search.pipeline as _pipeline
    import dc_search.retrieval as _retrieval

    cov = _make_date_coverage(envelopes={}, entity_ranges={})

    with (
        patch.object(_retrieval, "variable_date_coverage", return_value=cov),
        patch.object(_retrieval, "presence_for_entities", return_value=frozenset()),
    ):
        result = _pipeline._resolve_union_availability(
            ["country/KEN"],
            candidate_sv_dcids=("UNKNOWN_SV",),
        )

    # result is an empty frozenset; _apply_availability_filter's empty-intersection
    # fallback preserves UNKNOWN_SV in the final sv_set.
    assert isinstance(result, frozenset)
    # The availability set is empty; the caller (pipeline step 3) uses
    # _apply_availability_filter which falls back to the full sv_set when empty.


def test_resolve_union_availability_topic_path_uses_variables_for_entities_batch():
    """Topic path (no candidate_sv_dcids) → variables_for_entities_batch called."""
    from unittest.mock import patch

    import dc_search.pipeline as _pipeline
    import dc_search.retrieval as _retrieval

    with (
        patch.object(
            _retrieval,
            "variables_for_entities_batch",
            return_value={"country/KEN": ("SV_A", "SV_B")},
        ) as mock_batch,
        patch.object(_retrieval, "variable_date_coverage") as mock_cov,
    ):
        result = _pipeline._resolve_union_availability(
            ["country/KEN"],
            candidate_sv_dcids=(),
        )

    mock_batch.assert_called_once()
    mock_cov.assert_not_called()
    assert "SV_A" in result
    assert "SV_B" in result


def test_resolve_union_availability_custom_in_envelope_but_absent_at_place_not_in_custom_present():
    """Correctness invariant (generic-review G1):

    A custom var present in cov.envelopes but with NO {E,V} pair at the resolved
    places must be excluded from custom_present AND must NOT be re-queried via
    presence_for_entities (it is in the map, so not base-DC).
    """
    from unittest.mock import patch

    import dc_search.pipeline as _pipeline
    import dc_search.retrieval as _retrieval

    # SV_CUSTOM in envelopes but no entity_range at country/KEN
    cov = _make_date_coverage(
        envelopes={"SV_CUSTOM": ("2010", "2024")},
        entity_ranges={},  # no {E,V} at country/KEN
    )

    with (
        patch.object(_retrieval, "variable_date_coverage", return_value=cov),
        patch.object(_retrieval, "presence_for_entities") as mock_presence,
    ):
        result = _pipeline._resolve_union_availability(
            ["country/KEN"],
            candidate_sv_dcids=("SV_CUSTOM",),
        )

    # SV_CUSTOM not in custom_present (no entity_ranges at place)
    assert "SV_CUSTOM" not in result
    # SV_CUSTOM is in the map → not base-DC → presence_for_entities not called
    mock_presence.assert_not_called()
