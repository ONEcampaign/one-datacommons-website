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
from dc_search.slot_binding import BindResult
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
    """Patch all external I/O modules used by pipeline._run_one_variable and _run."""
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
    monkeypatch.setattr(
        _retrieval,
        "place_names_batch",
        lambda *, dcids: {},
    )

    # Place extraction → no places (clean availability path).
    monkeypatch.setattr(_shape_mod, "extract_place_tokens", lambda query: [])

    # bind returns a successful BindResult by default
    async def _mock_bind(shape_context, *, model=None):
        return BindResult(
            shape=_SHAPE, predicates=(_PREDICATE,), usage=_USAGE, defaulted_recipient=False
        )

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
        return BindResult(
            shape=_SHAPE, predicates=(_PREDICATE,), usage=_USAGE, defaulted_recipient=False
        )

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

    import dc_search.pipeline._run as _run

    original_run_one = _run._run_one_variable

    async def _capturing_run_one(
        variable,
        query,
        *,
        place_dcids,
        dates=None,
        entities=None,
        parent_to_children=None,
        slot_bind_usages,
    ):
        if variable is not None:
            processed.append(variable)
        return await original_run_one(
            variable,
            query,
            place_dcids=place_dcids,
            dates=dates,
            entities=entities,
            parent_to_children=parent_to_children,
            slot_bind_usages=slot_bind_usages,
        )

    monkeypatch.setattr(_run, "_run_one_variable", _capturing_run_one)

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
    import dc_search.pipeline._run as _run
    import dc_search.retrieval as _retrieval

    DELAY = 0.05
    N_VARS = 6

    six_vars = [f"var_{i}" for i in range(N_VARS)]

    async def _mock_extract(query, *, model=None):
        return (_make_extraction(six_vars), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)
    # Stub place resolution to avoid network hits.
    monkeypatch.setattr(_retrieval, "resolve_places_batch", lambda *, names: {})
    monkeypatch.setattr(_retrieval, "place_names_batch", lambda *, dcids: {})

    async def _slow_run_one(
        variable,
        query,
        *,
        place_dcids,
        dates=None,
        entities=None,
        parent_to_children=None,
        slot_bind_usages,
    ):
        await asyncio.sleep(DELAY)
        slot_bind_usages.append(_USAGE)
        answer = _ANSWER.model_copy(update={"variable_label": variable if variable else None})
        return _pipeline._VariableResult(outcome=answer, n_candidates=1, n_shapes=1)

    monkeypatch.setattr(_run, "_run_one_variable", _slow_run_one)

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
# _resolve_union_availability hybrid tests
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
    """Correctness invariant: A custom var present in cov.envelopes but with NO {E,V}
    pair at the resolved places must be excluded from custom_present AND must NOT be
    re-queried via presence_for_entities (it is in the map, so not base-DC).
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


# ===========================================================================
# _build_resolved_places: alternatives + fail-open on name error
# ===========================================================================


@pytest.mark.asyncio
async def test_build_resolved_places_populates_alternatives(monkeypatch):
    """_build_resolved_places: alternatives list is populated from extra
    resolve_places_batch candidates (rank-1 is primary; the rest become alternatives)."""
    import dc_search.retrieval as _retrieval
    from dc_search.interpretation import PlaceAlternative
    from dc_search.pipeline import _build_resolved_places
    from dc_search.retrieval import PlaceCandidate

    # Two candidates for "Kenya": country/KEN (primary) and nuts/KEN (alternative)
    def _mock_resolve_batch(*, names):
        return {
            "Kenya": (
                PlaceCandidate(dcid="country/KEN"),
                PlaceCandidate(dcid="nuts/KEN"),
            )
        }

    monkeypatch.setattr(_retrieval, "resolve_places_batch", _mock_resolve_batch)
    monkeypatch.setattr(
        _retrieval,
        "place_names_batch",
        lambda *, dcids: {"country/KEN": ("Kenya", "Country")},
    )

    async def _resolved():
        from dc_search.pipeline import PlaceResolution

        return PlaceResolution(
            dcids=("country/KEN",), parent_to_children={}, parent_to_child_type={}
        )

    dcid_task = asyncio.create_task(_resolved())
    places = await _build_resolved_places(["Kenya"], dcid_task)

    assert len(places) == 1
    rp = places[0]
    assert rp.dcid == "country/KEN"
    assert rp.name == "Kenya"
    assert rp.type == "Country"
    assert len(rp.alternatives) == 1
    assert isinstance(rp.alternatives[0], PlaceAlternative)
    assert rp.alternatives[0].dcid == "nuts/KEN"


@pytest.mark.asyncio
async def test_build_resolved_places_fail_open_on_name_error(monkeypatch):
    """_build_resolved_places returns places with name=None when name fetch fails."""
    import dc_search.retrieval as _retrieval
    from dc_search.pipeline import PlaceResolution, _build_resolved_places
    from dc_search.retrieval import PlaceCandidate

    # resolve_places_batch resolves Kenya → country/KEN so the entity lookup succeeds.
    monkeypatch.setattr(
        _retrieval,
        "resolve_places_batch",
        lambda *, names: {"Kenya": (PlaceCandidate(dcid="country/KEN"),)},
    )

    # place_names_batch raises — fail-open → name=None
    def _raise(*, dcids):
        raise RuntimeError("name fetch failed")

    monkeypatch.setattr(_retrieval, "place_names_batch", _raise)

    async def _resolved():
        return PlaceResolution(
            dcids=("country/KEN",), parent_to_children={}, parent_to_child_type={}
        )

    dcid_task = asyncio.create_task(_resolved())
    places = await _build_resolved_places(["Kenya"], dcid_task)

    assert len(places) == 1
    assert places[0].dcid == "country/KEN"
    assert places[0].name is None


# ===========================================================================
# Union date range: _resolve_union_availability_with_ranges
# ===========================================================================


def test_resolve_union_availability_with_ranges_two_places():
    """Two resolved places → date_range is unioned (min earliest / max latest)."""
    from unittest.mock import patch

    import dc_search.pipeline as _pipeline
    import dc_search.retrieval as _retrieval

    cov = _make_date_coverage(
        envelopes={"SV_CUSTOM": ("2005", "2024")},
        entity_ranges={
            ("SV_CUSTOM", "country/KEN"): ("2010", "2020"),
            ("SV_CUSTOM", "country/UGA"): ("2008", "2022"),
        },
    )

    with patch.object(_retrieval, "variable_date_coverage", return_value=cov):
        _, ranges, _ = _pipeline._resolve_union_availability_with_ranges(
            ["country/KEN", "country/UGA"],
            candidate_sv_dcids=("SV_CUSTOM",),
        )

    # Union: min(2010, 2008) = 2008; max(2020, 2022) = 2022
    assert "SV_CUSTOM" in ranges
    lo, hi = ranges["SV_CUSTOM"]
    assert lo == "2008", f"Expected earliest='2008', got {lo!r}"
    assert hi == "2022", f"Expected latest='2022', got {hi!r}"


def test_resolve_union_availability_with_ranges_base_dc_var_gets_range():
    """Base-DC var (absent from envelopes) → observation_facet_ranges populates date_range."""
    from unittest.mock import patch

    import dc_search.pipeline as _pipeline
    import dc_search.retrieval as _retrieval

    cov = _make_date_coverage(envelopes={}, entity_ranges={})

    with (
        patch.object(_retrieval, "variable_date_coverage", return_value=cov),
        patch.object(
            _retrieval,
            "observation_facet_ranges",
            return_value=(frozenset({"BASE_SV"}), {"BASE_SV": ("1960", "2024")}),
        ),
    ):
        avail, ranges, _ = _pipeline._resolve_union_availability_with_ranges(
            ["country/KEN"],
            candidate_sv_dcids=("BASE_SV",),
        )

    assert "BASE_SV" in avail
    assert "BASE_SV" in ranges, "Base-DC vars should now appear in the ranges dict"
    assert ranges["BASE_SV"] == ("1960", "2024")


def test_resolve_union_availability_with_ranges_base_dc_no_data_empty_range():
    """Base-DC var with no data → present=frozenset(), ranges={} from observation_facet_ranges."""
    from unittest.mock import patch

    import dc_search.pipeline as _pipeline
    import dc_search.retrieval as _retrieval

    cov = _make_date_coverage(envelopes={}, entity_ranges={})

    with (
        patch.object(_retrieval, "variable_date_coverage", return_value=cov),
        patch.object(
            _retrieval,
            "observation_facet_ranges",
            return_value=(frozenset(), {}),
        ),
    ):
        avail, ranges, _ = _pipeline._resolve_union_availability_with_ranges(
            ["country/KEN"],
            candidate_sv_dcids=("BASE_SV",),
        )

    assert "BASE_SV" not in avail
    assert "BASE_SV" not in ranges


def test_resolve_union_availability_with_ranges_base_dc_observation_facet_ranges_called():
    """_resolve_union_availability_with_ranges calls observation_facet_ranges (not
    presence_for_entities) for base-DC vars."""
    from unittest.mock import patch

    import dc_search.pipeline as _pipeline
    import dc_search.retrieval as _retrieval

    cov = _make_date_coverage(envelopes={}, entity_ranges={})

    with (
        patch.object(_retrieval, "variable_date_coverage", return_value=cov),
        patch.object(
            _retrieval,
            "observation_facet_ranges",
            return_value=(frozenset({"BASE_SV"}), {"BASE_SV": ("2000", "2020")}),
        ) as mock_facet,
        patch.object(_retrieval, "presence_for_entities") as mock_presence,
    ):
        _pipeline._resolve_union_availability_with_ranges(
            ["country/KEN", "country/UGA"],
            candidate_sv_dcids=("BASE_SV",),
        )

    mock_facet.assert_called_once_with(
        variable_dcids=("BASE_SV",),
        entity_dcids=("country/KEN", "country/UGA"),
    )
    mock_presence.assert_not_called()


# ===========================================================================
# _drain: interpretation assembly
# ===========================================================================


@pytest.mark.asyncio
async def test_drain_assembles_interpretation_from_interpretation_and_places_events():
    """_drain assembles QueryInterpretation from Interpretation + Places events."""
    from dc_search.events import Done, DoneTelemetry, Interpretation, Places, Result, Start
    from dc_search.extraction import ExtractedDate
    from dc_search.interpretation import QueryInterpretation, ResolvedPlace
    from dc_search.pipeline import _drain

    resolved_place = ResolvedPlace(input_name="Kenya", dcid="country/KEN", name="Kenya")
    extracted_date = ExtractedDate(kind="point", start="2020", end=None)

    async def _fake_stream():
        yield Start(query="test", mode="default")
        yield Interpretation(
            variables=["life expectancy"],
            entities=["Kenya"],
            dates=[extracted_date],
            expected_results=1,
            truncated=False,
        )
        yield Places(places=[resolved_place])
        yield Result(
            index=0,
            variable_label="life expectancy",
            outcome_kind="answer",
            answer=_ANSWER,
        )
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

    result = await _drain(_fake_stream(), "test")

    assert result.interpretation is not None
    assert isinstance(result.interpretation, QueryInterpretation)
    assert result.interpretation.variables == ["life expectancy"]
    assert len(result.interpretation.places) == 1
    assert result.interpretation.places[0].dcid == "country/KEN"
    assert len(result.interpretation.dates) == 1
    assert result.interpretation.dates[0].start == "2020"


@pytest.mark.asyncio
async def test_drain_degenerate_interpretation_simple_endpoint():
    """Simple endpoint: _drain assembles interpretation with empty variables/dates
    but Places populated (degenerate interpretation)."""
    from dc_search.events import Done, DoneTelemetry, Places, Result, Stage, Start
    from dc_search.interpretation import QueryInterpretation, ResolvedPlace
    from dc_search.pipeline import _drain

    resolved_place = ResolvedPlace(input_name="Kenya", dcid="country/KEN")

    async def _fake_simple_stream():
        yield Start(query="test", mode="simple")
        yield Stage(stage="retrieving")
        yield Places(places=[resolved_place])
        yield Result(
            index=0,
            variable_label=None,
            outcome_kind="answer",
            answer=_ANSWER,
        )
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

    result = await _drain(_fake_simple_stream(), "test")

    assert result.interpretation is not None
    assert isinstance(result.interpretation, QueryInterpretation)
    # Simple endpoint: no Interpretation event → variables and dates are empty
    assert result.interpretation.variables == []
    assert result.interpretation.dates == []
    # Places event was present → places populated
    assert len(result.interpretation.places) == 1
    assert result.interpretation.places[0].dcid == "country/KEN"


@pytest.mark.asyncio
async def test_drain_no_interpretation_when_no_events():
    """_drain returns interpretation=None when neither Interpretation nor Places event emitted."""
    from dc_search.events import Done, DoneTelemetry, Result, Start
    from dc_search.pipeline import _drain

    async def _bare_stream():
        yield Start(query="test", mode="simple")
        yield Result(
            index=0,
            variable_label=None,
            outcome_kind="answer",
            answer=_ANSWER,
        )
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

    result = await _drain(_bare_stream(), "test")
    assert result.interpretation is None


# ===========================================================================
# answer_kind on topic short-circuit path
# ===========================================================================


@pytest.mark.asyncio
async def test_topic_short_circuit_answer_has_topic_kind(monkeypatch):
    """Topic-dominance path: answer_kind=="topic" + topic_name populated."""
    import dc_search.retrieval as _retrieval
    from dc_search.retrieval import IndicatorCandidate, TopicMetadata

    _patch_all(monkeypatch)

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
    monkeypatch.setattr(
        _retrieval, "resolve_indicator", lambda *, query, k: (topic_cand, non_topic_cand)
    )

    monkeypatch.setattr(
        _retrieval,
        "topic_metadata_batch",
        lambda *, dcids: {
            "dc/topic/Health": TopicMetadata(
                dcid="dc/topic/Health",
                name="Health",
                description="Health indicators.",
            )
        },
    )

    from dc_search import pipeline

    result = await pipeline.run_simple("health indicators in Kenya")

    assert result.terminated_by == "answer"
    assert len(result.answers) == 1
    a = result.answers[0]
    assert a.answer_kind == "topic"
    assert a.topic_name == "Health"
    assert a.topic_description == "Health indicators."


@pytest.mark.asyncio
async def test_ordinary_answer_has_variables_kind(monkeypatch):
    """Non-topic answer has answer_kind=="variables" (the default)."""
    import dc_search.extraction as _ext

    _patch_all(monkeypatch)

    async def _mock_extract(query, *, model=None):
        return (_make_extraction(["life expectancy"]), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)

    from dc_search import pipeline

    result = await pipeline.run_default(_QUERY)

    assert result.terminated_by == "answer"
    for a in result.answers:
        assert a.answer_kind == "variables"


@pytest.mark.asyncio
async def test_fan_out_scopes_shape_query_to_variable(monkeypatch):
    """Multi-variable fan-out feeds slot-binding the per-variable phrase (plus
    entities), NOT the full multi-variable query — otherwise sibling variables
    bias shape election toward broad catch-all topics (see the unemployment →
    dc/topic/Economy regression).
    """
    import dc_search.extraction as _ext
    import dc_search.slot_binding as _sb

    _patch_all(monkeypatch)

    # Extraction splits into two variables with one place; the original query
    # is deliberately distinct from any single focused phrase.
    def _extraction() -> QueryExtraction:
        return QueryExtraction(
            entities=["Kenya"], dates=[], variables=["life expectancy", "population"]
        )

    async def _mock_extract(query, *, model=None):
        return (_extraction(), _EXTRACT_USAGE)

    monkeypatch.setattr(_ext, "extract", _mock_extract)

    seen_queries: list[str] = []

    async def _bind_spy(shape_context, *, model=None):
        seen_queries.append(shape_context.query)
        return BindResult(
            shape=_SHAPE, predicates=(_PREDICATE,), usage=_USAGE, defaulted_recipient=False
        )

    monkeypatch.setattr(_sb, "bind", _bind_spy)

    from dc_search import pipeline

    await pipeline.run_default("life expectancy and population in Kenya")

    # Each variable is scoped to its own phrase + the extracted place; the full
    # query never reaches slot-binding.
    assert set(seen_queries) == {"life expectancy in Kenya", "population in Kenya"}
    assert "life expectancy and population in Kenya" not in seen_queries


@pytest.mark.asyncio
async def test_topic_short_circuit_enriches_member_variables(monkeypatch):
    """Topic-dominance short-circuit fetches member features so the expanded
    variables carry name/description, not bare DCIDs (regression: SDG-topic
    members rendered as raw `sdg/VC_DTH_*` strings).
    """
    import dc_search.hooks as _hooks
    import dc_search.retrieval as _retrieval

    topic_cand = IndicatorCandidate(dcid="dc/topic/Health", type_of=["Topic"], score=1.0)
    non_topic_cand = IndicatorCandidate(
        dcid="LifeExpectancy_Person", type_of=["StatisticalVariable"], score=0.3
    )
    _patch_all(monkeypatch, retrieval_candidates=(topic_cand, non_topic_cand))

    members = ["LifeExpectancy_Person", "Count_Person"]

    # materialize_many returns a topic answer with bare members (no variables yet);
    # the short-circuit is responsible for enriching them.
    topic_answer = AnswerCollection(
        predicate=Predicate(
            population_type=None,
            measured_property=None,
            constraints={"relevantTopic": "dc/topic/Health"},
        ),
        sv_set=members,
        confidence="high",
        caveats=["topic_expanded"],
    )
    monkeypatch.setattr(
        _hooks, "materialize_many", lambda predicates, candidates, *, ctx: topic_answer
    )

    # Distinct features per member, each with a human-readable name.
    member_feats = {
        "LifeExpectancy_Person": StatVarFeatures(
            dcid="LifeExpectancy_Person", name="Life expectancy"
        ),
        "Count_Person": StatVarFeatures(dcid="Count_Person", name="Total population"),
    }
    monkeypatch.setattr(_retrieval, "stat_var_features_batch", lambda *, sv_dcids: member_feats)

    from dc_search import pipeline

    result = await pipeline.run_simple("health indicators")

    assert result.terminated_by == "answer"
    answer = result.answers[0]
    assert answer.answer_kind == "topic"
    # Members are enriched: names present, in sv_set order.
    assert [v.dcid for v in answer.variables] == members
    assert {v.dcid: v.name for v in answer.variables} == {
        "LifeExpectancy_Person": "Life expectancy",
        "Count_Person": "Total population",
    }


# ===========================================================================
# Characterization tests: place-role-aware CRS_DAC binding
# ===========================================================================

# Shared CRS_DAC stubs for the three characterization cases.

_CRS_INDICATOR_CAND = IndicatorCandidate(
    dcid="ONE/CRS_DAC/Malariacontrol-ODAGrants-USA",
    type_of=["StatisticalVariable"],
    score=0.9,
)

_CRS_SHAPE = Shape(
    population_type="DevelopmentFinance",
    measured_property="amount",
    constraint_keys=("DevelopmentFinanceRecipient",),
    member_dcids=("ONE/CRS_DAC/Malariacontrol-ODAGrants-USA",),
    slot_taxonomy={"DevelopmentFinanceRecipient": ("country/JOR", "country/TGO", "country/NGA")},
    is_topic=False,
)

_CENSUS_SHAPE = Shape(
    population_type="MortalityEvent",
    measured_property="count",
    constraint_keys=(),
    member_dcids=("Count_MortalityEvent_Person_Malaria",),
    slot_taxonomy={},
    is_topic=False,
)

_CENSUS_PREDICATE = Predicate(
    population_type="MortalityEvent",
    measured_property="count",
    constraints={},
)


def _patch_crs_common(monkeypatch):
    """Patch infrastructure shared across all three CRS characterization cases.

    Also stubs out variable_date_coverage + observation_facet_ranges so the
    availability re-rank never hits the network.  Individual tests override
    variable_date_coverage when they need specific availability data.
    """
    import dc_search.retrieval as _retrieval
    import dc_search.shape as _shape_mod

    monkeypatch.setattr(_retrieval, "resolve_indicator", lambda *, query, k: (_CRS_INDICATOR_CAND,))
    monkeypatch.setattr(_retrieval, "topic_metadata_batch", lambda *, dcids: {})
    monkeypatch.setattr(
        _retrieval, "presence_for_entities", lambda *, variable_dcids, entity_dcids: frozenset()
    )
    monkeypatch.setattr(_retrieval, "variables_for_entities_batch", lambda *, entity_dcids: {})
    monkeypatch.setattr(_shape_mod, "extract_place_tokens", lambda query: [])
    # Default: empty coverage map — no network calls.
    monkeypatch.setattr(
        _retrieval,
        "variable_date_coverage",
        lambda *, variable_dcids, entity_dcids: _make_date_coverage(envelopes={}, entity_ranges={}),
    )
    # observation_facet_ranges must never reach the real mixer.
    monkeypatch.setattr(
        _retrieval,
        "observation_facet_ranges",
        lambda *, variable_dcids, entity_dcids: (frozenset(), {}),
    )


@pytest.mark.asyncio
async def test_default_grants_from_us_to_togo(monkeypatch):
    """Case 1: 'grants from us to togo' — explicit donor + recipient.

    Donor = country/USA (observation entity); recipient = country/TGO (constraint).
    Variable name contains "Grants to Togo"; available_at_place is True;
    interpreted_place_as_recipient NOT in caveats.
    """
    import dc_search.extraction as _ext
    import dc_search.hooks as _hooks
    import dc_search.retrieval as _retrieval
    import dc_search.slot_binding as _sb

    _patch_crs_common(monkeypatch)

    # USA and Togo both resolved.
    monkeypatch.setattr(
        _retrieval,
        "resolve_places_batch",
        lambda *, names: {
            "us": (_make_place_candidate("country/USA"),),
            "togo": (_make_place_candidate("country/TGO"),),
        },
    )
    monkeypatch.setattr(
        _retrieval,
        "place_names_batch",
        lambda *, dcids: {
            "country/USA": ("United States", "Country"),
            "country/TGO": ("Togo", "Country"),
        },
    )

    # Bind: TGO → recipient slot; defaulted_recipient=False (explicit "to togo").
    recipient_predicate = Predicate(
        population_type="DevelopmentFinance",
        measured_property="amount",
        constraints={"DevelopmentFinanceRecipient": "country/TGO"},
    )

    async def _mock_bind(shape_context, *, model=None):
        return BindResult(
            shape=_CRS_SHAPE,
            predicates=(recipient_predicate,),
            usage=_USAGE,
            defaulted_recipient=False,
        )

    monkeypatch.setattr(_sb, "bind", _mock_bind)
    monkeypatch.setattr(_sb, "get_last_usage", lambda: _USAGE)

    # Recovered SV for Togo; USA has data (available_at_place=True).
    togo_dcid = "ONE/CRS_DAC/Malariacontrol-ODAGrants-TGO"
    togo_features = StatVarFeatures(
        dcid=togo_dcid,
        name="Health [Grants to Togo]",
        population_type=["DevelopmentFinance"],
        measured_property=["amount"],
    )

    # materialize returns the Togo sv (Piece D path).
    togo_answer = AnswerCollection(
        predicate=recipient_predicate,
        sv_set=[togo_dcid],
        confidence="high",
    )
    monkeypatch.setattr(
        _hooks, "materialize_many", lambda predicates, candidates, *, ctx: togo_answer
    )

    # stat_var_features_batch returns Togo features (backup fetch in post-materialize).
    monkeypatch.setattr(
        _retrieval,
        "stat_var_features_batch",
        lambda *, sv_dcids: {d: togo_features for d in sv_dcids},
    )

    # Availability for USA donor over Togo SV → present.
    monkeypatch.setattr(
        _retrieval,
        "variable_date_coverage",
        lambda *, variable_dcids, entity_dcids: _make_date_coverage(
            envelopes={togo_dcid: ("2007", "2024")},
            entity_ranges={(togo_dcid, "country/USA"): ("2007", "2024")},
        ),
    )

    async def _mock_extract(query, *, model=None):
        return (
            QueryExtraction(entities=["us", "togo"], variables=["grants"], dates=[]),
            _EXTRACT_USAGE,
        )

    monkeypatch.setattr(_ext, "extract", _mock_extract)

    from dc_search import pipeline

    result = await pipeline.run_default("grants from us to togo")

    assert result.terminated_by == "answer"
    assert len(result.answers) == 1
    answer = result.answers[0]

    # Explicit assertions — not just "matches run_simple".
    # Predicate carries the TGO recipient constraint.
    assert answer.predicate.constraints.get("DevelopmentFinanceRecipient") == "country/TGO"

    # Variable name contains "Grants to Togo" (feature fetched for recovered DCID).
    var_names = [v.name for v in answer.variables if v.name]
    assert any("Togo" in n for n in var_names), f"Expected a 'Togo' variable; got {var_names}"

    # Donor (USA) named → available_at_place is True.
    assert any(v.available_at_place is True for v in answer.variables), (
        "Expected available_at_place=True for country/USA donor"
    )

    # No ambiguous default → caveat absent.
    assert "interpreted_place_as_recipient" not in answer.caveats


def _make_place_candidate(dcid):
    """Minimal PlaceCandidate for resolve_places_batch stubs."""
    from dc_search.retrieval import PlaceCandidate

    return PlaceCandidate(dcid=dcid)


@pytest.mark.asyncio
async def test_default_malaria_grants_nigeria(monkeypatch):
    """Case 2: 'malaria grants nigeria' — unqualified place → defaulted recipient.

    Recipient = country/NGA (by default, no donor named);
    available_at_place is None; date_range is None;
    interpreted_place_as_recipient in caveats;
    terminated_by == 'answer' (NOT ask/under_specified).
    """
    import dc_search.extraction as _ext
    import dc_search.hooks as _hooks
    import dc_search.retrieval as _retrieval
    import dc_search.slot_binding as _sb

    _patch_crs_common(monkeypatch)

    # Only Nigeria resolved.
    monkeypatch.setattr(
        _retrieval,
        "resolve_places_batch",
        lambda *, names: {"nigeria": (_make_place_candidate("country/NGA"),)},
    )
    monkeypatch.setattr(
        _retrieval,
        "place_names_batch",
        lambda *, dcids: {"country/NGA": ("Nigeria", "Country")},
    )

    # Bind: NGA → recipient slot; defaulted_recipient=True (ambiguous query).
    nga_predicate = Predicate(
        population_type="DevelopmentFinance",
        measured_property="amount",
        constraints={"DevelopmentFinanceRecipient": "country/NGA"},
    )

    async def _mock_bind(shape_context, *, model=None):
        return BindResult(
            shape=_CRS_SHAPE,
            predicates=(nga_predicate,),
            usage=_USAGE,
            defaulted_recipient=True,
        )

    monkeypatch.setattr(_sb, "bind", _mock_bind)
    monkeypatch.setattr(_sb, "get_last_usage", lambda: _USAGE)

    # Recovered SV for Nigeria.
    nga_dcid = "ONE/CRS_DAC/Malariacontrol-ODAGrants-NGA"
    nga_features = StatVarFeatures(
        dcid=nga_dcid,
        name="Health [Grants to Nigeria]",
        population_type=["DevelopmentFinance"],
        measured_property=["amount"],
    )

    nga_answer = AnswerCollection(
        predicate=nga_predicate,
        sv_set=[nga_dcid],
        confidence="high",
    )
    monkeypatch.setattr(
        _hooks, "materialize_many", lambda predicates, candidates, *, ctx: nga_answer
    )

    monkeypatch.setattr(
        _retrieval,
        "stat_var_features_batch",
        lambda *, sv_dcids: {d: nga_features for d in sv_dcids},
    )

    # No donor → variable_date_coverage not called (donor set empty) — safe to leave unreachable.
    monkeypatch.setattr(
        _retrieval,
        "variable_date_coverage",
        lambda *, variable_dcids, entity_dcids: _make_date_coverage(envelopes={}, entity_ranges={}),
    )

    async def _mock_extract(query, *, model=None):
        return (
            QueryExtraction(entities=["nigeria"], variables=["malaria grants"], dates=[]),
            _EXTRACT_USAGE,
        )

    monkeypatch.setattr(_ext, "extract", _mock_extract)

    from dc_search import pipeline

    result = await pipeline.run_default("malaria grants nigeria")

    assert result.terminated_by == "answer", f"Expected 'answer', got {result.terminated_by!r}"
    assert len(result.answers) == 1
    answer = result.answers[0]

    # Recipient constraint present.
    assert answer.predicate.constraints.get("DevelopmentFinanceRecipient") == "country/NGA"

    # Variable name contains "Grants to Nigeria".
    var_names = [v.name for v in answer.variables if v.name]
    assert any("Nigeria" in n for n in var_names), f"Expected a 'Nigeria' variable; got {var_names}"

    # No donor named → availability omitted (None).
    got_avail = [v.available_at_place for v in answer.variables]
    assert all(a is None for a in got_avail), (
        f"Expected available_at_place=None for all vars; got {got_avail}"
    )
    assert all(v.date_range is None for v in answer.variables), (
        f"Expected date_range=None for all vars; got {[v.date_range for v in answer.variables]}"
    )

    # Ambiguous default → caveat present.
    assert "interpreted_place_as_recipient" in answer.caveats, (
        f"Expected 'interpreted_place_as_recipient' in caveats; got {answer.caveats}"
    )


@pytest.mark.asyncio
async def test_default_malaria_deaths_nigeria_census_regression(monkeypatch):
    """Case 3: 'malaria deaths nigeria' — Census shape, no recipient binding.

    entity country/NGA stays in the donor set; no interpreted_place_as_recipient caveat.
    """
    import dc_search.extraction as _ext
    import dc_search.hooks as _hooks
    import dc_search.retrieval as _retrieval
    import dc_search.slot_binding as _sb

    # Use Census indicators for this query.
    census_cand = IndicatorCandidate(
        dcid="Count_MortalityEvent_Person_Malaria",
        type_of=["StatisticalVariable"],
        score=0.9,
    )
    monkeypatch.setattr(_retrieval, "resolve_indicator", lambda *, query, k: (census_cand,))
    monkeypatch.setattr(_retrieval, "topic_metadata_batch", lambda *, dcids: {})
    monkeypatch.setattr(
        _retrieval, "presence_for_entities", lambda *, variable_dcids, entity_dcids: frozenset()
    )
    monkeypatch.setattr(_retrieval, "variables_for_entities_batch", lambda *, entity_dcids: {})

    import dc_search.shape as _shape_mod

    monkeypatch.setattr(_shape_mod, "extract_place_tokens", lambda query: [])

    # Nigeria resolved.
    monkeypatch.setattr(
        _retrieval,
        "resolve_places_batch",
        lambda *, names: {"nigeria": (_make_place_candidate("country/NGA"),)},
    )
    monkeypatch.setattr(
        _retrieval,
        "place_names_batch",
        lambda *, dcids: {"country/NGA": ("Nigeria", "Country")},
    )

    # Census bind: no recipient slot, no defaulted_recipient.
    async def _mock_bind(shape_context, *, model=None):
        return BindResult(
            shape=_CENSUS_SHAPE,
            predicates=(_CENSUS_PREDICATE,),
            usage=_USAGE,
            defaulted_recipient=False,
        )

    monkeypatch.setattr(_sb, "bind", _mock_bind)
    monkeypatch.setattr(_sb, "get_last_usage", lambda: _USAGE)

    census_features = StatVarFeatures(
        dcid="Count_MortalityEvent_Person_Malaria",
        name="Malaria Deaths",
        population_type=["MortalityEvent"],
        measured_property=["count"],
    )
    monkeypatch.setattr(
        _retrieval,
        "stat_var_features_batch",
        lambda *, sv_dcids: {d: census_features for d in sv_dcids},
    )

    census_answer = AnswerCollection(
        predicate=_CENSUS_PREDICATE,
        sv_set=["Count_MortalityEvent_Person_Malaria"],
        confidence="high",
    )

    # Track what place_dcids reach materialize_many.
    received_place_dcids: list[tuple[str, ...]] = []

    def _capturing_materialize(predicates, candidates, *, ctx):
        received_place_dcids.append(ctx.place_dcids)
        return census_answer

    monkeypatch.setattr(_hooks, "materialize_many", _capturing_materialize)

    monkeypatch.setattr(
        _retrieval,
        "variable_date_coverage",
        lambda *, variable_dcids, entity_dcids: _make_date_coverage(envelopes={}, entity_ranges={}),
    )

    async def _mock_extract(query, *, model=None):
        return (
            QueryExtraction(entities=["nigeria"], variables=["malaria deaths"], dates=[]),
            _EXTRACT_USAGE,
        )

    monkeypatch.setattr(_ext, "extract", _mock_extract)

    from dc_search import pipeline

    result = await pipeline.run_default("malaria deaths nigeria")

    assert result.terminated_by == "answer"
    assert len(result.answers) == 1
    answer = result.answers[0]

    # NGA must be in the donor set (no recipient binding in Census shape).
    assert len(received_place_dcids) == 1
    assert "country/NGA" in received_place_dcids[0], (
        f"country/NGA must reach materialize as an entity; got {received_place_dcids[0]}"
    )

    # No ambiguous default → caveat absent.
    assert "interpreted_place_as_recipient" not in answer.caveats, (
        f"Unexpected caveat in Census regression: {answer.caveats}"
    )


# ===========================================================================
# _build_resolved_places_triples: surface↔DCID alignment with unresolved entity
# ===========================================================================


@pytest.mark.asyncio
async def test_build_resolved_places_triples_alignment_with_unresolved_middle(monkeypatch):
    """When a middle entity fails to resolve, the surviving 4-tuples carry their OWN
    entity's surface string — not the adjacent entity's.

    entities = ["us", "notaplace", "Togo"]
      - "us" → country/USA (resolves)
      - "notaplace" → no candidates (skipped)
      - "Togo" → country/TGO (resolves)

    Expected 4-tuples (dcid, canonical_name, input_surface, role):
      ("country/USA", "United States", "us", <role>)
      ("country/TGO", "Togo", "Togo", <role>)

    The bug: the old zip-style loop would pair country/TGO with "notaplace" as surface.
    Role defaults to "ambiguous" when query="" (no directional grammar).
    """
    import dc_search.retrieval as _retrieval
    from dc_search.pipeline._run import _build_resolved_places_triples
    from dc_search.retrieval import PlaceCandidate

    def _mock_resolve_batch(*, names):
        return {
            "us": (PlaceCandidate(dcid="country/USA"),),
            # "notaplace" absent → no candidates
            "Togo": (PlaceCandidate(dcid="country/TGO"),),
        }

    monkeypatch.setattr(_retrieval, "resolve_places_batch", _mock_resolve_batch)
    monkeypatch.setattr(
        _retrieval,
        "place_names_batch",
        lambda *, dcids: {
            "country/USA": ("United States", "Country"),
            "country/TGO": ("Togo", "Country"),
        },
    )

    triples = await _build_resolved_places_triples(
        place_dcids=["country/USA", "country/TGO"],
        entities=["us", "notaplace", "Togo"],
        query="",  # empty query → all roles "ambiguous"
    )

    assert len(triples) == 2, f"Expected 2 4-tuples, got {len(triples)}: {triples}"

    # Each element is (dcid, canonical_name, input_surface, role).
    dcid_to_surface = {t[0]: t[2] for t in triples}
    assert dcid_to_surface["country/USA"] == "us", (
        f"USA 4-tuple carries wrong surface: {dcid_to_surface['country/USA']!r}"
    )
    assert dcid_to_surface["country/TGO"] == "Togo", (
        f"Togo 4-tuple carries wrong surface: {dcid_to_surface['country/TGO']!r}"
    )

    dcid_to_name = {t[0]: t[1] for t in triples}
    assert dcid_to_name["country/USA"] == "United States"
    assert dcid_to_name["country/TGO"] == "Togo"

    # Role field (index 3) is present; "ambiguous" with empty query.
    dcid_to_role = {t[0]: t[3] for t in triples}
    assert dcid_to_role["country/USA"] in ("donor", "recipient", "ambiguous")
    assert dcid_to_role["country/TGO"] in ("donor", "recipient", "ambiguous")


# ===========================================================================
# Critical integration test: real bind + precomputed roles
# ===========================================================================
# This test exercises the REAL slot_binding.bind (LLM mocked, not bind mocked)
# so the post-correction runs off the precomputed role in resolved_places.
# It would have caught the fan-out vs. directional-role conflict: even though
# shape_context.query is the scoped "grants in us, Togo", the role is read
# from the 4-tuple (computed from the original query) — so USA stays donor
# and TGO is forced into the recipient slot.
# ===========================================================================


@pytest.mark.asyncio
async def test_real_bind_directional_role_from_precomputed_4tuple(monkeypatch):
    """Real bind reads pre-computed role from 4-tuple.

    Query: "grants from us to togo"
    - Extraction: variables=["grants"], entities=["us", "togo"]
    - Resolved: country/USA (role=donor), country/TGO (role=recipient)
    - shape_context.query is scoped to "grants in us, togo" (fan-out query)
      which strips "from"/"to" grammar — the old code would call
      place_directional_role on that scoped query and return "ambiguous" for both.
    - With Amendment 2, role is read from the 4-tuple pre-computed from the
      original full query, so USA is correctly excluded (donor) and TGO is
      forced into the recipient slot (recipient).

    Asserts:
    - DevelopmentFinanceRecipient == country/TGO
    - country/USA is NOT the recipient (it is the donor entity)
    - interpreted_place_as_recipient NOT in caveats (explicit "to togo" cue)
    """
    import dc_search.extraction as _ext
    import dc_search.hooks as _hooks
    import dc_search.retrieval as _retrieval

    _patch_crs_common(monkeypatch)

    # USA and Togo both resolved, with their correct surface strings.
    monkeypatch.setattr(
        _retrieval,
        "resolve_places_batch",
        lambda *, names: {
            "us": (_make_place_candidate("country/USA"),),
            "togo": (_make_place_candidate("country/TGO"),),
        },
    )
    monkeypatch.setattr(
        _retrieval,
        "place_names_batch",
        lambda *, dcids: {
            "country/USA": ("United States", "Country"),
            "country/TGO": ("Togo", "Country"),
        },
    )

    # resolve_indicator returns the CRS_DAC candidate (with TGO in taxonomy).
    monkeypatch.setattr(
        _retrieval,
        "resolve_indicator",
        lambda *, query, k: (_CRS_INDICATOR_CAND,),
    )

    # stat_var_features_batch returns features with TGO in taxonomy.
    crs_features = StatVarFeatures(
        dcid="ONE/CRS_DAC/Malariacontrol-ODAGrants-USA",
        name="Malaria grants",
        population_type=["DevelopmentFinance"],
        measured_property=["amount"],
        constraints={
            "DevelopmentFinanceRecipient": ["country/JOR", "country/TGO", "country/NGA"],
            "DevelopmentFinancePurpose": ["DAC/Malariacontrol"],
            "DevelopmentFinanceScheme": ["ODAGrants"],
        },
    )
    monkeypatch.setattr(
        _retrieval,
        "stat_var_features_batch",
        lambda *, sv_dcids: {d: crs_features for d in sv_dcids},
    )

    # LLM returns: recipient=null (it only sees the scoped query "grants in us, togo"
    # with no directional grammar). The REAL bind post-correction must force TGO in
    # via the precomputed role="recipient" in the 4-tuple.
    from unittest.mock import AsyncMock, patch

    from dc_search.slot_binding import _Output, _SlotBinding

    llm_output = _Output(
        chosen_shape_index=0,
        bindings=[
            _SlotBinding(slot="DevelopmentFinanceRecipient", value=None),
            _SlotBinding(slot="DevelopmentFinancePurpose", value="DAC/Malariacontrol"),
            _SlotBinding(slot="DevelopmentFinanceScheme", value="ODAGrants"),
        ],
    )
    from dc_search.telemetry import Usage

    mock_generate = AsyncMock(
        return_value=(
            llm_output,
            Usage(input_tokens=10, output_tokens=5, model="test"),
        )
    )

    # materialize_many: capture what predicate it receives; return an answer.
    received_predicates: list = []

    def _capturing_materialize(predicates, candidates, *, ctx):
        received_predicates.extend(predicates)
        # Return a minimal AnswerCollection matching the first predicate.
        from dc_search.predicate import AnswerCollection

        return AnswerCollection(
            predicate=predicates[0],
            sv_set=["ONE/CRS_DAC/Malariacontrol-ODAGrants-TGO"],
            confidence="high",
        )

    monkeypatch.setattr(_hooks, "materialize_many", _capturing_materialize)

    async def _mock_extract(query, *, model=None):
        return (
            QueryExtraction(entities=["us", "togo"], variables=["grants"], dates=[]),
            _EXTRACT_USAGE,
        )

    monkeypatch.setattr(_ext, "extract", _mock_extract)

    from dc_search import pipeline

    with patch("dc_search.slot_binding.llm.generate_structured", mock_generate):
        result = await pipeline.run_default("grants from us to togo")

    assert result.terminated_by == "answer", f"Expected 'answer', got {result.terminated_by!r}"
    assert len(received_predicates) >= 1, "materialize_many was not called"

    # The post-correction must have forced TGO into the recipient slot
    # (role="recipient" precomputed from "grants from us to togo").
    recipient = received_predicates[0].constraints.get("DevelopmentFinanceRecipient")
    assert recipient == "country/TGO", (
        f"Expected country/TGO as recipient; got {recipient!r}. "
        "The precomputed role must override the LLM's null binding."
    )

    # USA must NOT be in the recipient slot — it is the donor entity.
    assert recipient != "country/USA"

    # No ambiguous default → interpreted_place_as_recipient NOT in caveats.
    assert len(result.answers) >= 1
    for a in result.answers:
        assert "interpreted_place_as_recipient" not in a.caveats, (
            f"Unexpected caveat: 'interpreted_place_as_recipient' should be absent "
            f"when role='recipient' was explicit (not defaulted). Got: {a.caveats}"
        )


# ===========================================================================
# contained-in expansion tests
# ===========================================================================


@pytest.mark.asyncio
async def test_expansion_pipeline_adds_children_to_resolved_set(monkeypatch):
    """contained_in=True expands country/USA → states are added to the resolved set
    alongside the parent.

    Asserts:
    - resolved place_dcids passed to _run_one_variable contains country/USA, geoId/01, geoId/06
    - the Places event's ResolvedPlace for country/USA has expanded=True, child_type=="State",
      and children listing the two state children
    - child_places_batch was called with child_type="State"
    """
    import dc_search.extraction as _ext
    import dc_search.pipeline._run as _run
    import dc_search.retrieval as _retrieval
    from dc_search.extraction import QueryExtraction
    from dc_search.retrieval import PlaceCandidate

    _patch_all(monkeypatch)

    # LLM extracts contained_in=True with USA as parent.
    async def _mock_extract(query, *, model=None):
        return (
            QueryExtraction(
                variables=["poverty rate"],
                entities=["United States"],
                dates=[],
                contained_in=True,
            ),
            _EXTRACT_USAGE,
        )

    monkeypatch.setattr(_ext, "extract", _mock_extract)

    # Resolve "United States" → country/USA (Country type).
    monkeypatch.setattr(
        _retrieval,
        "resolve_places_batch",
        lambda *, names: {"United States": (PlaceCandidate(dcid="country/USA"),)},
    )

    # place_names_batch returns names for parent + two children.
    monkeypatch.setattr(
        _retrieval,
        "place_names_batch",
        lambda *, dcids: {
            "country/USA": ("United States", "Country"),
            "geoId/01": ("Alabama", "State"),
            "geoId/06": ("California", "State"),
        },
    )

    # child_places_batch returns Alabama + California for USA (sorted by dcid).
    child_batch_calls: list[dict] = []

    def _mock_child_places_batch(*, parent_dcids, child_type, cap=200):
        child_batch_calls.append({"parent_dcids": parent_dcids, "child_type": child_type})
        return {
            "country/USA": (
                ("geoId/01", "Alabama"),
                ("geoId/06", "California"),
            )
        }

    monkeypatch.setattr(_retrieval, "child_places_batch", _mock_child_places_batch)
    # parent_countries_batch: USA is a Country type, so it won't be called for country lookup.
    monkeypatch.setattr(_retrieval, "parent_countries_batch", lambda *, parent_dcids: {})

    # Capture what place_dcids _run_one_variable receives.
    received_place_dcids: list[list[str]] = []
    original_run_one = _run._run_one_variable

    async def _capturing_run_one(
        variable,
        query,
        *,
        place_dcids,
        dates=None,
        entities=None,
        parent_to_children=None,
        slot_bind_usages,
    ):
        received_place_dcids.append(list(place_dcids))
        return await original_run_one(
            variable,
            query,
            place_dcids=place_dcids,
            dates=dates,
            entities=entities,
            parent_to_children=parent_to_children,
            slot_bind_usages=slot_bind_usages,
        )

    monkeypatch.setattr(_run, "_run_one_variable", _capturing_run_one)

    from dc_search import pipeline

    # Collect SSE events to inspect Places.
    events = []
    async for event in pipeline.stream_default("poverty rate in US states"):
        events.append(event)

    # Assert (1): resolved place_dcids contains all three DCIDs.
    assert len(received_place_dcids) == 1, (
        f"Expected one _run_one_variable call; got {len(received_place_dcids)}"
    )
    dcids_sent = received_place_dcids[0]
    assert "country/USA" in dcids_sent, f"country/USA missing from {dcids_sent}"
    assert "geoId/01" in dcids_sent, f"geoId/01 missing from {dcids_sent}"
    assert "geoId/06" in dcids_sent, f"geoId/06 missing from {dcids_sent}"

    # Assert (2): the Places event's ResolvedPlace for USA has expansion fields set.
    from dc_search.events import Places

    places_events = [e for e in events if isinstance(e, Places)]
    assert len(places_events) == 1, f"Expected 1 Places event; got {len(places_events)}"
    resolved_places = places_events[0].places
    usa_place = next((p for p in resolved_places if p.dcid == "country/USA"), None)
    assert usa_place is not None, "country/USA missing from Places event"
    assert usa_place.expanded is True, "expected expanded=True for country/USA"
    assert usa_place.child_type == "State", (
        f"expected child_type='State'; got {usa_place.child_type!r}"
    )
    child_dcids = [c.dcid for c in usa_place.children]
    assert "geoId/01" in child_dcids, f"geoId/01 missing from children: {child_dcids}"
    assert "geoId/06" in child_dcids, f"geoId/06 missing from children: {child_dcids}"

    # Assert (3): child_places_batch called with child_type="State".
    assert len(child_batch_calls) == 1, (
        f"Expected 1 child_places_batch call; got {len(child_batch_calls)}"
    )
    assert child_batch_calls[0]["child_type"] == "State", (
        f"Expected child_type='State'; got {child_batch_calls[0]['child_type']!r}"
    )


@pytest.mark.asyncio
async def test_expansion_back_compat_no_child_fetch_when_contained_in_false(monkeypatch):
    """contained_in=False (default) must make zero child_places_batch / parent_countries_batch
    calls and return PlaceResolution with empty maps — byte-identical to the pre-expansion path.

    Also verifies that the Places event ResolvedPlaces have expanded=False and empty children.
    """
    import dc_search.extraction as _ext
    import dc_search.retrieval as _retrieval
    from dc_search.extraction import QueryExtraction
    from dc_search.retrieval import PlaceCandidate

    _patch_all(monkeypatch)

    # LLM extraction with contained_in defaulting to False.
    async def _mock_extract(query, *, model=None):
        return (
            QueryExtraction(
                variables=["poverty rate"],
                entities=["Kenya"],
                dates=[],
                contained_in=False,
            ),
            _EXTRACT_USAGE,
        )

    monkeypatch.setattr(_ext, "extract", _mock_extract)

    monkeypatch.setattr(
        _retrieval,
        "resolve_places_batch",
        lambda *, names: {"Kenya": (PlaceCandidate(dcid="country/KEN"),)},
    )
    monkeypatch.setattr(
        _retrieval,
        "place_names_batch",
        lambda *, dcids: {"country/KEN": ("Kenya", "Country")},
    )

    # Spy on child_places_batch and parent_countries_batch — must never be called.
    child_batch_call_count = 0
    countries_batch_call_count = 0

    def _spy_child_places(*, parent_dcids, child_type, cap=200):
        nonlocal child_batch_call_count
        child_batch_call_count += 1
        return {}

    def _spy_parent_countries(*, parent_dcids):
        nonlocal countries_batch_call_count
        countries_batch_call_count += 1
        return {}

    monkeypatch.setattr(_retrieval, "child_places_batch", _spy_child_places)
    monkeypatch.setattr(_retrieval, "parent_countries_batch", _spy_parent_countries)

    from dc_search import pipeline

    events = []
    async for event in pipeline.stream_default("poverty rate in Kenya"):
        events.append(event)

    # Zero child/country fetches.
    assert child_batch_call_count == 0, (
        f"child_places_batch must not be called when contained_in=False; "
        f"got {child_batch_call_count} calls"
    )
    assert countries_batch_call_count == 0, (
        f"parent_countries_batch must not be called when contained_in=False; "
        f"got {countries_batch_call_count} calls"
    )

    # Places event: non-expanded ResolvedPlace.
    from dc_search.events import Places

    places_events = [e for e in events if isinstance(e, Places)]
    assert len(places_events) == 1
    resolved_places = places_events[0].places
    assert len(resolved_places) == 1
    rp = resolved_places[0]
    assert rp.dcid == "country/KEN"
    assert rp.expanded is False, f"expected expanded=False; got {rp.expanded!r}"
    assert rp.children == [], f"expected empty children; got {rp.children!r}"


@pytest.mark.asyncio
async def test_build_resolved_places_triples_children_get_ambiguous_role():
    """CRS directional + expansion: children appended via parent_to_children
    land with role='ambiguous' and the parent's existing directional role is unchanged.

    Focused unit test on _build_resolved_places_triples with a parent_to_children map
    representing the contained-in expansion scenario.
    """
    from dc_search.pipeline._run import _build_resolved_places_triples
    from dc_search.retrieval import PlaceCandidate

    # "grants from us" — USA is the donor.
    query = "grants from us"

    def _mock_resolve_batch(*, names):
        return {"us": (PlaceCandidate(dcid="country/USA"),)}

    from unittest.mock import patch

    names_map = {
        "country/USA": ("United States", "Country"),
        "geoId/01": ("Alabama", "State"),
        "geoId/06": ("California", "State"),
    }

    with (
        patch("dc_search.retrieval.resolve_places_batch", _mock_resolve_batch),
        patch(
            "dc_search.retrieval.place_names_batch",
            lambda *, dcids: {k: v for k, v in names_map.items() if k in dcids},
        ),
    ):
        triples = await _build_resolved_places_triples(
            place_dcids=["country/USA", "geoId/01", "geoId/06"],
            entities=["us"],
            query=query,
            parent_to_children={
                "country/USA": (
                    ("geoId/01", "Alabama"),
                    ("geoId/06", "California"),
                )
            },
        )

    # Parent (USA) should have a directional role (donor given "from us").
    dcid_to_role = {t[0]: t[3] for t in triples}
    assert "country/USA" in dcid_to_role, "USA parent must be in 4-tuples"
    # USA should be "donor" from "grants from us" grammar.
    assert dcid_to_role["country/USA"] == "donor", (
        f"Expected USA role='donor' from 'grants from us'; got {dcid_to_role['country/USA']!r}"
    )

    # Children must be present with role="ambiguous".
    assert "geoId/01" in dcid_to_role, "geoId/01 child must be in 4-tuples"
    assert "geoId/06" in dcid_to_role, "geoId/06 child must be in 4-tuples"
    assert dcid_to_role["geoId/01"] == "ambiguous", (
        f"Expected geoId/01 role='ambiguous'; got {dcid_to_role['geoId/01']!r}"
    )
    assert dcid_to_role["geoId/06"] == "ambiguous", (
        f"Expected geoId/06 role='ambiguous'; got {dcid_to_role['geoId/06']!r}"
    )

    # Children have input_surface=None (they were never typed by the user).
    child_surfaces = {t[0]: t[2] for t in triples if t[0] in ("geoId/01", "geoId/06")}
    assert child_surfaces["geoId/01"] is None
    assert child_surfaces["geoId/06"] is None
