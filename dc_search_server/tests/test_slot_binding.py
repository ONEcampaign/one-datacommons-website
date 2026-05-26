"""Tests for dc_search.slot_binding — mocked llm.generate_structured responses.

All tests are deterministic: no real API calls are made.
``llm.generate_structured`` is patched with an AsyncMock throughout.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from dc_search.predicate import AskClarification, Predicate
from dc_search.shape import ShapeContext, build_shape_context
from dc_search.slot_binding import (
    BindResult,
    _explode_constraints,
    _Output,
    _SlotBinding,
    bind,
    get_last_usage,
    get_last_user_message,
)
from dc_search.telemetry import Usage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DUMMY_USAGE = Usage(
    input_tokens=10,
    output_tokens=5,
    model="gemini-flash-lite-latest",
    model_requests=1,
)


def _make_output(
    chosen_shape_index: int,
    constraints: dict[str, Any],
    ask: str | None = None,
) -> _Output:
    """Build an _Output from a flat constraints dict (test convenience helper)."""
    bindings = [_SlotBinding(slot=k, value=v) for k, v in constraints.items()]
    return _Output(chosen_shape_index=chosen_shape_index, bindings=bindings, ask=ask)


def _make_generate_structured(
    chosen_shape_index: int,
    constraints: dict[str, Any],
    ask: str | None = None,
) -> AsyncMock:
    """Return an AsyncMock for llm.generate_structured that returns a scripted _Output."""
    parsed = _make_output(chosen_shape_index, constraints, ask)
    mock = AsyncMock(return_value=(parsed, _DUMMY_USAGE))
    return mock


def _make_raising_generate_structured(exc: Exception) -> AsyncMock:
    """Return an AsyncMock for llm.generate_structured that raises exc."""
    mock = AsyncMock(side_effect=exc)
    return mock


# ---------------------------------------------------------------------------
# Fixtures: pre-built ShapeContexts from conftest candidates
# ---------------------------------------------------------------------------


@pytest.fixture
def crs_dac_shape_context(crs_dac_candidates):
    return build_shape_context(
        "ODA grants for malaria control in Kenya",
        crs_dac_candidates,
    )


@pytest.fixture
def census_shape_context(census_candidates):
    return build_shape_context("female population in Togo", census_candidates)


# ---------------------------------------------------------------------------
# Test 1: fully-bound CRS_DAC predicate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_crs_dac_fully_bound(crs_dac_shape_context: ShapeContext) -> None:
    """Happy-path: model returns a fully-bound CRS_DAC predicate."""
    mock = _make_generate_structured(
        0,
        {
            "DevelopmentFinancePurpose": "DAC/Malariacontrol",
            "DevelopmentFinanceRecipient": "country/KEN",
            "DevelopmentFinanceScheme": "ODAGrants",
        },
    )

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(crs_dac_shape_context)

    assert isinstance(result, BindResult), f"Expected BindResult, got {type(result)}"
    assert isinstance(result.predicates, tuple) and len(result.predicates) == 1
    predicate = result.predicates[0]

    assert result.shape.population_type == "DevelopmentFinance"
    assert predicate.constraints["DevelopmentFinancePurpose"] == "DAC/Malariacontrol"
    assert predicate.constraints["DevelopmentFinanceRecipient"] == "country/KEN"
    assert predicate.constraints["DevelopmentFinanceScheme"] == "ODAGrants"
    assert isinstance(result.usage, Usage)
    assert result.usage.model_requests >= 1


# ---------------------------------------------------------------------------
# Test 2: wildcard recipient (intentional null)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_crs_dac_wildcard_recipient(crs_dac_shape_context: ShapeContext) -> None:
    """Wildcard is preserved when model leaves a slot as null."""
    mock = _make_generate_structured(
        0,
        {
            "DevelopmentFinancePurpose": "DAC/Malariacontrol",
            "DevelopmentFinanceRecipient": None,
            "DevelopmentFinanceScheme": "ODAGrants",
        },
    )

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(crs_dac_shape_context)

    assert isinstance(result, BindResult)
    assert isinstance(result.predicates, tuple) and len(result.predicates) == 1
    predicate = result.predicates[0]
    assert predicate.constraints.get("DevelopmentFinanceRecipient") is None, (
        "Wildcard recipient must be preserved as None"
    )


# ---------------------------------------------------------------------------
# Test 3: census namespace with constraint binding
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_census_with_constraints(census_shape_context: ShapeContext) -> None:
    """Census shape: model binds gender slot to Female."""
    shape_with_gender = next(
        (s for s in census_shape_context.shapes if "gender" in s.slot_taxonomy),
        None,
    )
    if shape_with_gender is None:
        shape_idx = 0
    else:
        shape_idx = census_shape_context.shapes.index(shape_with_gender)

    mock = _make_generate_structured(shape_idx, {"gender": "Female"})

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(census_shape_context)

    assert isinstance(result, BindResult)
    assert isinstance(result.predicates, tuple) and len(result.predicates) == 1
    predicate = result.predicates[0]
    assert predicate.constraints.get("gender") == "Female"


# ---------------------------------------------------------------------------
# Test 4: model returns ask → AskClarification(reason="under_specified")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_returns_ask_when_model_asks(crs_dac_shape_context: ShapeContext) -> None:
    """When the model sets ask=..., bind returns AskClarification(reason=under_specified)."""
    mock = _make_generate_structured(0, {}, ask="Please specify the recipient country.")

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(crs_dac_shape_context)

    assert isinstance(result, AskClarification)
    assert result.reason == "under_specified"
    assert "specify" in result.message.lower() or "recipient" in result.message.lower()


# ---------------------------------------------------------------------------
# Test 5: parse error → AskClarification(reason="parse_error") — I10 verification
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_returns_ask_on_parse_error(crs_dac_shape_context: ShapeContext) -> None:
    """When generate_structured raises, bind returns AskClarification with a FIXED message.

    The exception text must NOT appear in the returned AskClarification.message
    to avoid leaking internal SDK details to callers.
    """
    sensitive_detail = "internal model error including SDK details"
    mock = _make_raising_generate_structured(Exception(sensitive_detail))

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(crs_dac_shape_context)

    assert isinstance(result, AskClarification)
    assert result.reason == "parse_error"
    # The fixed message must not leak exception text.
    assert sensitive_detail not in result.message
    assert result.message == "The search model could not process this query. Try rephrasing."


# ---------------------------------------------------------------------------
# Test 6: empty shapes → AskClarification(reason="retrieval_weak")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_returns_ask_on_empty_shapes() -> None:
    """Empty ShapeContext.shapes returns AskClarification(reason=retrieval_weak)."""
    empty_ctx = ShapeContext(
        query="some query",
        shapes=(),
        keyword_cues={},
    )
    # No LLM call should happen; no mock needed.
    result = await bind(empty_ctx)

    assert isinstance(result, AskClarification)
    assert result.reason == "retrieval_weak"


# ---------------------------------------------------------------------------
# Test 7: out-of-range index → AskClarification(reason="ambiguous_shape")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_out_of_range_index(crs_dac_shape_context: ShapeContext) -> None:
    """Model returns chosen_shape_index=999 → AskClarification(reason=ambiguous_shape)."""
    mock = _make_generate_structured(999, {})

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(crs_dac_shape_context)

    assert isinstance(result, AskClarification)
    assert result.reason == "ambiguous_shape"


# ---------------------------------------------------------------------------
# Test 8: singleton list normalised to scalar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_normalises_singleton_list(crs_dac_shape_context: ShapeContext) -> None:
    """LLM emits a 1-element list; bind must normalise it to a plain string."""
    mock = _make_generate_structured(0, {"DevelopmentFinanceRecipient": ["country/KEN"]})

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(crs_dac_shape_context)

    assert isinstance(result, BindResult)
    assert isinstance(result.predicates, tuple) and len(result.predicates) == 1
    predicate = result.predicates[0]
    assert predicate.constraints["DevelopmentFinanceRecipient"] == "country/KEN", (
        "Singleton list must be collapsed to a scalar string"
    )


# ---------------------------------------------------------------------------
# Tests for _explode_constraints
# ---------------------------------------------------------------------------


def test_explode_singleton_list_normalised_to_scalar() -> None:
    """1-element list is normalised to a scalar; returns a 1-tuple."""
    result = _explode_constraints({"X": ["a"]})
    assert result == ({"X": "a"},)


def test_explode_cross_product() -> None:
    """2-value list cross-producted with scalar yields 2 scalar dicts."""
    result = _explode_constraints({"X": ["a", "b"], "Y": "z"})
    assert len(result) == 2
    assert {"X": "a", "Y": "z"} in result
    assert {"X": "b", "Y": "z"} in result


def test_explode_cap_overflow_wildcards_largest_list() -> None:
    """A list of 50 values exceeds _MAX_PREDICATES; replaced with None (wildcard)."""
    big_list = [f"val{i}" for i in range(50)]
    result = _explode_constraints({"slot": big_list})
    assert result == ({"slot": None},)


def test_explode_empty_dict_returns_one_empty_predicate() -> None:
    """Empty input returns a 1-tuple of one empty dict."""
    result = _explode_constraints({})
    assert result == ({},)


# ---------------------------------------------------------------------------
# Test 9: multi-value bind returns a tuple of Predicates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_returns_tuple_of_predicates_multi_value(
    crs_dac_shape_context: ShapeContext,
) -> None:
    """LLM emits a 2-element list; bind returns a 2-tuple of Predicates."""
    mock = _make_generate_structured(
        0,
        {
            "DevelopmentFinancePurpose": "DAC/Malariacontrol",
            "DevelopmentFinanceRecipient": ["country/KEN", "country/TGO"],
            "DevelopmentFinanceScheme": "ODAGrants",
        },
    )

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(crs_dac_shape_context)

    assert isinstance(result, BindResult)
    assert isinstance(result.predicates, tuple)
    assert len(result.predicates) == 2
    recipients = {p.constraints["DevelopmentFinanceRecipient"] for p in result.predicates}
    assert recipients == {"country/KEN", "country/TGO"}
    for p in result.predicates:
        assert p.constraints["DevelopmentFinancePurpose"] == "DAC/Malariacontrol"
        assert p.constraints["DevelopmentFinanceScheme"] == "ODAGrants"


@pytest.mark.asyncio
async def test_bind_returns_singleton_tuple_single_value(
    crs_dac_shape_context: ShapeContext,
) -> None:
    """Single-value binding returns a 1-tuple of Predicates."""
    mock = _make_generate_structured(
        0,
        {
            "DevelopmentFinancePurpose": "DAC/Malariacontrol",
            "DevelopmentFinanceRecipient": "country/KEN",
            "DevelopmentFinanceScheme": "ODAGrants",
        },
    )

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(crs_dac_shape_context)

    assert isinstance(result, BindResult)
    assert isinstance(result.predicates, tuple)
    assert len(result.predicates) == 1
    assert isinstance(result.predicates[0], Predicate)
    assert result.predicates[0].constraints["DevelopmentFinanceRecipient"] == "country/KEN"


# ---------------------------------------------------------------------------
# Test 10: CONCURRENCY — ContextVars are isolated per asyncio Task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contextvar_isolation_across_concurrent_tasks(
    crs_dac_candidates,
    census_candidates,
) -> None:
    """8 concurrent bind() calls each see their own ContextVar values.

    Spawns 8 tasks on different ShapeContexts; each task's generate_structured
    mock returns a distinct user message (via the query embedded in the
    ShapeContext). After all tasks complete, we verify via per-task callbacks
    that get_last_user_message() returned a value that matches the task's own
    query — proving ContextVar isolation across concurrent tasks.

    This test catches the bug where module-level globals would cause the last
    concurrent write to overwrite values seen by earlier reads.
    """
    # Build 8 distinct shape contexts with distinct queries.
    queries = [f"test query number {i} for isolation check" for i in range(8)]
    # Alternate between crs_dac and census candidates.
    contexts = [
        build_shape_context(q, crs_dac_candidates if i % 2 == 0 else census_candidates)
        for i, q in enumerate(queries)
    ]

    # Recorder: populated inside each task's own Context, so we can verify
    # which task saw which user_message.
    _TEST_RECORDER: list[tuple[str, str]] = []

    async def run_one(query: str, ctx: ShapeContext) -> None:
        usage = Usage(model="test", model_requests=1)
        parsed = _Output(chosen_shape_index=0, bindings=[])
        mock = AsyncMock(return_value=(parsed, usage))

        with patch("dc_search.slot_binding.llm.generate_structured", mock):
            await bind(ctx)

        # Read ContextVar value while still inside this task's context.
        msg = get_last_user_message()
        _TEST_RECORDER.append((query, msg or ""))

    # Run all 8 tasks concurrently.
    await asyncio.gather(*(run_one(q, c) for q, c in zip(queries, contexts)))

    # Each task should have seen its own query embedded in get_last_user_message().
    assert len(_TEST_RECORDER) == 8
    for task_query, seen_message in _TEST_RECORDER:
        assert task_query in seen_message, (
            f"Task for query {task_query!r} saw wrong user message: {seen_message!r}"
        )


# ---------------------------------------------------------------------------
# Test 11: usage ContextVar set even when model signals ask
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_usage_contextvar_set_on_ask_path(crs_dac_shape_context: ShapeContext) -> None:
    """Usage ContextVar is set even when model returns ask.

    When the LLM succeeds but returns ask=..., the usage should still be
    captured in the ContextVar so pipeline.py can record token telemetry.
    """
    mock = _make_generate_structured(0, {}, ask="Please clarify the recipient country.")

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(crs_dac_shape_context)

    # Result is AskClarification, but usage should still be captured.
    assert isinstance(result, AskClarification)
    assert result.reason == "under_specified"
    usage = get_last_usage()
    assert usage is not None, "Usage ContextVar must be set even on the ask path"
    assert isinstance(usage, Usage)


# ---------------------------------------------------------------------------
# Test 12: ask message is capped at 500 chars
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ask_message_truncated_at_500_chars(crs_dac_shape_context: ShapeContext) -> None:
    """output.ask is capped at 500 chars in the HTTP response."""
    long_ask = "A" * 600
    mock = _make_generate_structured(0, {}, ask=long_ask)

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(crs_dac_shape_context)

    assert isinstance(result, AskClarification)
    assert result.reason == "under_specified"
    assert len(result.message) == 500, (
        f"ask message must be capped at 500 chars; got {len(result.message)}"
    )


# ---------------------------------------------------------------------------
# Test 13: _Output schema must NOT contain additionalProperties
# ---------------------------------------------------------------------------


def test_output_schema_no_additional_properties() -> None:
    """_Output.model_json_schema() must not contain 'additionalProperties': True.

    google-genai Developer API rejects schemas that emit additionalProperties,
    which Pydantic generates for dict[str, X] fields.
    """
    import json

    schema_str = json.dumps(_Output.model_json_schema())
    assert '"additionalProperties": true' not in schema_str.lower().replace(" ", ""), (
        "Schema must not contain additionalProperties: true — "
        "google-genai Developer API will reject it"
    )


# ---------------------------------------------------------------------------
# S4 tests: place-role offer + DevelopmentFinance default correction
# ---------------------------------------------------------------------------


def _crs_dac_ctx_with_places(
    query: str,
    resolved_places: tuple[tuple[str, str | None, str | None, str], ...],
    crs_dac_candidates,
) -> ShapeContext:
    """Build a CRS_DAC ShapeContext pre-loaded with resolved_places (4-tuples with role)."""
    return build_shape_context(query, crs_dac_candidates, resolved_places=resolved_places)


def _census_ctx_with_places(
    query: str,
    resolved_places: tuple[tuple[str, str | None, str | None, str], ...],
    census_candidates,
) -> ShapeContext:
    """Build a Census ShapeContext pre-loaded with resolved_places (4-tuples with role)."""
    return build_shape_context(query, census_candidates, resolved_places=resolved_places)


@pytest.mark.asyncio
async def test_devfinance_unqualified_place_defaults_to_recipient(
    crs_dac_candidates,
) -> None:
    """Unqualified 'nigeria' (no from/to cue) → NGA bound as recipient.

    defaulted_recipient must be True.
    """
    ctx = _crs_dac_ctx_with_places(
        "malaria grants nigeria",
        (("country/NGA", "Nigeria", "nigeria", "ambiguous"),),
        crs_dac_candidates,
    )
    # LLM leaves recipient null (no taxonomy match for NGA in retrieved pool).
    mock = _make_generate_structured(
        0,
        {
            "DevelopmentFinancePurpose": "DAC/Malariacontrol",
            "DevelopmentFinanceRecipient": None,
            "DevelopmentFinanceScheme": "ODAGrants",
        },
    )

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(ctx)

    assert isinstance(result, BindResult)
    assert result.defaulted_recipient is True
    assert len(result.predicates) == 1
    assert result.predicates[0].constraints["DevelopmentFinanceRecipient"] == "country/NGA"


@pytest.mark.asyncio
async def test_devfinance_explicit_to_binds_recipient_no_default(
    crs_dac_candidates,
) -> None:
    """Explicit 'to nigeria' → NGA bound as recipient; defaulted_recipient is False."""
    ctx = _crs_dac_ctx_with_places(
        "malaria grants to nigeria",
        (("country/NGA", "Nigeria", "nigeria", "recipient"),),
        crs_dac_candidates,
    )
    # LLM sets recipient (it saw it in prompt or taxonomy).
    mock = _make_generate_structured(
        0,
        {
            "DevelopmentFinancePurpose": "DAC/Malariacontrol",
            "DevelopmentFinanceRecipient": "country/NGA",
            "DevelopmentFinanceScheme": "ODAGrants",
        },
    )

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(ctx)

    assert isinstance(result, BindResult)
    assert result.defaulted_recipient is False
    assert result.predicates[0].constraints["DevelopmentFinanceRecipient"] == "country/NGA"


@pytest.mark.asyncio
async def test_devfinance_from_place_not_bound_as_recipient(
    crs_dac_candidates,
) -> None:
    """'from the united states' → USA must NOT be bound to recipient slot."""
    ctx = _crs_dac_ctx_with_places(
        "malaria grants from the united states",
        (("country/USA", "United States", "the united states", "donor"),),
        crs_dac_candidates,
    )
    # LLM incorrectly binds USA as recipient (post-correction must clear it).
    mock = _make_generate_structured(
        0,
        {
            "DevelopmentFinancePurpose": "DAC/Malariacontrol",
            "DevelopmentFinanceRecipient": "country/USA",
            "DevelopmentFinanceScheme": "ODAGrants",
        },
    )

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(ctx)

    assert isinstance(result, BindResult)
    assert result.defaulted_recipient is False
    # USA must not be in the recipient slot — it's a donor.
    assert result.predicates[0].constraints.get("DevelopmentFinanceRecipient") is None


@pytest.mark.asyncio
async def test_devfinance_grants_from_us_to_togo(
    crs_dac_candidates,
) -> None:
    """'grants from us to togo': USA donor (not bound), TGO recipient; defaulted_recipient False.

    This exercises the input_surface anchor — 'us' is the verbatim token and
    would not match canonical_name 'United States' or DCID slug 'USA'.
    """
    ctx = _crs_dac_ctx_with_places(
        "grants from us to togo",
        (
            ("country/USA", "United States", "us", "donor"),
            ("country/TGO", "Togo", "Togo", "recipient"),
        ),
        crs_dac_candidates,
    )
    # LLM may bind either or neither; post-correction is deterministic.
    mock = _make_generate_structured(
        0,
        {
            "DevelopmentFinancePurpose": None,
            "DevelopmentFinanceRecipient": None,
            "DevelopmentFinanceScheme": None,
        },
    )

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(ctx)

    assert isinstance(result, BindResult)
    assert result.defaulted_recipient is False
    recipient = result.predicates[0].constraints.get("DevelopmentFinanceRecipient")
    assert recipient == "country/TGO", f"Expected country/TGO as recipient, got {recipient!r}"
    # USA must NOT be the recipient — it is the donor entity.
    assert recipient != "country/USA"


@pytest.mark.asyncio
async def test_non_devfinance_offered_place_slot_untouched(
    census_candidates,
) -> None:
    """Non-DevelopmentFinance shape: offered place + null slot → no correction.

    defaulted_recipient must be False.
    """
    # Census shape has no place-typed slot taxonomy — DevelopmentFinanceRecipient etc.
    # don't appear. Even if we attach resolved_places, the post-correction must not fire.
    ctx = _census_ctx_with_places(
        "female population in nigeria",
        (("country/NGA", "Nigeria", "nigeria", "ambiguous"),),
        census_candidates,
    )
    mock = _make_generate_structured(0, {"gender": "Female"})

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(ctx)

    assert isinstance(result, BindResult)
    assert result.defaulted_recipient is False
    # Gender slot must be bound as the LLM said, without interference.
    assert result.predicates[0].constraints.get("gender") == "Female"


@pytest.mark.asyncio
async def test_build_user_message_includes_user_named_places_for_crs_dac(
    crs_dac_candidates,
) -> None:
    """user_named_places block appears in the prompt when a resolved place is on-taxonomy."""
    from dc_search.slot_binding import get_last_user_message

    ctx = _crs_dac_ctx_with_places(
        "malaria grants nigeria",
        (("country/NGA", "Nigeria", "nigeria", "ambiguous"),),
        crs_dac_candidates,
    )
    mock = _make_generate_structured(
        0,
        {
            "DevelopmentFinancePurpose": "DAC/Malariacontrol",
            "DevelopmentFinanceRecipient": None,
            "DevelopmentFinanceScheme": "ODAGrants",
        },
    )

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        await bind(ctx)

    msg = get_last_user_message()
    assert msg is not None
    assert "user_named_places" in msg, "user_named_places block must appear in the prompt"
    assert "country/NGA" in msg


@pytest.mark.asyncio
async def test_build_user_message_omits_user_named_places_for_census(
    census_candidates,
) -> None:
    """user_named_places block is absent when no resolved place matches any slot's taxonomy."""
    from dc_search.slot_binding import get_last_user_message

    # Census slots (gender, causeOfDeath, medicalCondition) don't have country/* taxonomy.
    ctx = _census_ctx_with_places(
        "female population in nigeria",
        (("country/NGA", "Nigeria", "nigeria", "ambiguous"),),
        census_candidates,
    )
    mock = _make_generate_structured(0, {"gender": "Female"})

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        await bind(ctx)

    msg = get_last_user_message()
    assert msg is not None
    assert "user_named_places" not in msg, (
        "user_named_places must not appear when no place is on-taxonomy for Census slots"
    )


# ---------------------------------------------------------------------------
# Issue 2: defaulted_recipient when the LLM itself returns the offered DCID
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_devfinance_ambiguous_llm_binds_offered_dcid_sets_defaulted_recipient(
    crs_dac_candidates,
) -> None:
    """Ambiguous query: the LLM returns the offered DCID itself → defaulted_recipient True.

    'malaria grants nigeria' has no directional cue → role="ambiguous".
    The LLM pro-actively binds country/NGA to the recipient slot.  The else
    branch must still set defaulted_recipient=True because the binding was
    driven by the unqualified-place offer, not by deterministic directional
    language — so the interpreted_place_as_recipient caveat must be emitted.
    """
    ctx = _crs_dac_ctx_with_places(
        "malaria grants nigeria",
        (("country/NGA", "Nigeria", "nigeria", "ambiguous"),),
        crs_dac_candidates,
    )
    # LLM explicitly binds country/NGA (the offered DCID) — current != None case.
    mock = _make_generate_structured(
        0,
        {
            "DevelopmentFinancePurpose": "DAC/Malariacontrol",
            "DevelopmentFinanceRecipient": "country/NGA",
            "DevelopmentFinanceScheme": "ODAGrants",
        },
    )

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(ctx)

    assert isinstance(result, BindResult)
    assert result.defaulted_recipient is True, (
        "defaulted_recipient must be True when the LLM binds the offered DCID on an "
        "ambiguous (no directional cue) query — the caveat must not be suppressed"
    )
    assert result.predicates[0].constraints["DevelopmentFinanceRecipient"] == "country/NGA"
