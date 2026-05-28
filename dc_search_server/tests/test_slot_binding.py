"""Tests for dc_search.slot_binding — deterministic with mocked LLM.

llm.generate_structured is patched with an AsyncMock throughout.
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
# Test helpers
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
    """Build an _Output from a flat constraints dict."""
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
# Fully-bound predicate path
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


@pytest.mark.asyncio
async def test_bind_threads_cache_name_into_llm_call(
    crs_dac_shape_context: ShapeContext,
) -> None:
    """bind passes the explicit-cache name from get_system_cache to generate_structured."""
    gen_mock = _make_generate_structured(0, {"DevelopmentFinanceScheme": "ODAGrants"})
    cache_mock = AsyncMock(return_value="cachedContents/slot-bind")

    with (
        patch("dc_search.slot_binding.llm.generate_structured", gen_mock),
        patch("dc_search.slot_binding.llm.get_system_cache", cache_mock),
    ):
        await bind(crs_dac_shape_context)

    assert gen_mock.await_args.kwargs["cached_content"] == "cachedContents/slot-bind"


# ---------------------------------------------------------------------------
# Wildcard recipient path
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
# Census namespace with constraints
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
# Model ask signal: AskClarification
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
# Parse error path: fixed message (no SDK details leaked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bind_returns_ask_on_parse_error(crs_dac_shape_context: ShapeContext) -> None:
    """When generate_structured raises, bind returns AskClarification with fixed message.

    Exception text is not exposed to avoid leaking internal SDK details.
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
# Empty shapes: retrieval_weak AskClarification
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
# Out-of-range index: ambiguous_shape AskClarification
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
# Singleton list normalization
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
# Multi-value binding: predicate tuple
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
# ContextVar isolation across concurrent tasks
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contextvar_isolation_across_concurrent_tasks(
    crs_dac_candidates,
    census_candidates,
) -> None:
    """8 concurrent bind() calls each see their own ContextVar values.

    Spawns 8 tasks on different ShapeContexts and verifies get_last_user_message()
    matches each task's query — proving ContextVar isolation across concurrent tasks.
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
# Usage ContextVar set on ask path
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
# Ask message truncation at 500 chars
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
# _Output schema validation
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
# Place-role offer and default correction
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

    defaulted_recipient is True (caveat needed on ambiguous place).
    """
    ctx = _crs_dac_ctx_with_places(
        "malaria grants nigeria",
        (("country/NGA", "Nigeria", "nigeria", "ambiguous"),),
        crs_dac_candidates,
    )
    # LLM leaves recipient null (no on-taxonomy match for NGA).
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
    # LLM sets recipient explicitly.
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
    # LLM may bind USA as recipient (post-correction clears it).
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
    """'grants from us to togo': USA donor cleared, TGO recipient set.

    Exercises input_surface matching for abbreviated place names ('us' ≠ 'USA').
    """
    ctx = _crs_dac_ctx_with_places(
        "grants from us to togo",
        (
            ("country/USA", "United States", "us", "donor"),
            ("country/TGO", "Togo", "Togo", "recipient"),
        ),
        crs_dac_candidates,
    )
    # LLM output varies; post-correction is deterministic.
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


# ---------------------------------------------------------------------------
# Set-binding: recipient parent with children
# ---------------------------------------------------------------------------


def _crs_dac_ctx_with_places_and_contained_in(
    query: str,
    resolved_places: tuple[tuple[str, str | None, str | None, str], ...],
    parent_to_children: dict[str, tuple[tuple[str, str | None], ...]],
    crs_dac_candidates,
    *,
    contained_in: bool = True,
) -> ShapeContext:
    """Build a CRS_DAC ShapeContext with contained_in + parent_to_children set."""
    return build_shape_context(
        query,
        crs_dac_candidates,
        resolved_places=resolved_places,
        contained_in=contained_in,
        parent_to_children=parent_to_children,
    )


@pytest.mark.asyncio
async def test_set_binding_recipient_parent_with_children(
    crs_dac_candidates,
) -> None:
    """Parent 'DAC/Africa' recipient + contained_in + children KEN/TGO.

    constraints holds parent, constraint_sets holds children frozenset.
    """
    ctx = _crs_dac_ctx_with_places_and_contained_in(
        "malaria grants to african countries",
        (
            ("DAC/Africa", "Africa", "african countries", "recipient"),
            ("country/KEN", "Kenya", None, "ambiguous"),
            ("country/TGO", "Togo", None, "ambiguous"),
        ),
        {"DAC/Africa": (("country/KEN", "Kenya"), ("country/TGO", "Togo"))},
        crs_dac_candidates,
    )
    mock = _make_generate_structured(
        0,
        {
            "DevelopmentFinancePurpose": "DAC/Malariacontrol",
            "DevelopmentFinanceRecipient": "DAC/Africa",
            "DevelopmentFinanceScheme": "ODAGrants",
        },
    )

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(ctx)

    assert isinstance(result, BindResult)
    assert len(result.predicates) == 1
    pred = result.predicates[0]
    # Parent aggregate stays in scalar constraints.
    assert pred.constraints["DevelopmentFinanceRecipient"] == "DAC/Africa"
    # Children are in constraint_sets.
    assert pred.constraint_sets.get("DevelopmentFinanceRecipient") == frozenset(
        {"country/KEN", "country/TGO"}
    )


@pytest.mark.asyncio
async def test_set_binding_scalar_unchanged_contained_in_false(
    crs_dac_candidates,
) -> None:
    """Scalar path: 'malaria grants to Nigeria', contained_in=False.

    No set binding occurs; constraint_sets is empty.
    """
    ctx = _crs_dac_ctx_with_places_and_contained_in(
        "malaria grants to Nigeria",
        (("country/NGA", "Nigeria", "Nigeria", "recipient"),),
        {},
        crs_dac_candidates,
        contained_in=False,
    )
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
    assert len(result.predicates) == 1
    pred = result.predicates[0]
    assert pred.constraints["DevelopmentFinanceRecipient"] == "country/NGA"
    assert pred.constraint_sets == {}, "No set binding when contained_in is False"


@pytest.mark.asyncio
async def test_set_binding_donor_contained_in_does_not_trigger(
    crs_dac_candidates,
) -> None:
    """Donor contained-in: 'grants from african countries'.

    Parent role is 'donor'; set-binding must NOT fire. constraint_sets == {}.
    """
    ctx = _crs_dac_ctx_with_places_and_contained_in(
        "grants from african countries",
        (
            ("DAC/Africa", "Africa", "african countries", "donor"),
            ("country/KEN", "Kenya", None, "ambiguous"),
            ("country/TGO", "Togo", None, "ambiguous"),
        ),
        {"DAC/Africa": (("country/KEN", "Kenya"), ("country/TGO", "Togo"))},
        crs_dac_candidates,
    )
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
    for pred in result.predicates:
        assert pred.constraint_sets == {}, (
            "Donor contained-in must NOT produce a recipient constraint_sets"
        )


@pytest.mark.asyncio
async def test_set_binding_mixed_from_germany_to_african_countries(
    crs_dac_candidates,
) -> None:
    """Mixed: 'from Germany to african countries'.

    Germany (donor) cleared from recipient; Africa (recipient) set as parent + children.
    """
    ctx = _crs_dac_ctx_with_places_and_contained_in(
        "grants from Germany to african countries",
        (
            ("country/DEU", "Germany", "Germany", "donor"),
            ("DAC/Africa", "Africa", "african countries", "recipient"),
            ("country/KEN", "Kenya", None, "ambiguous"),
            ("country/TGO", "Togo", None, "ambiguous"),
        ),
        {"DAC/Africa": (("country/KEN", "Kenya"), ("country/TGO", "Togo"))},
        crs_dac_candidates,
    )
    # LLM may incorrectly bind Germany as recipient; post-correction clears it.
    # Africa is then set as the recipient.
    mock = _make_generate_structured(
        0,
        {
            "DevelopmentFinancePurpose": "DAC/Malariacontrol",
            "DevelopmentFinanceRecipient": "country/DEU",
            "DevelopmentFinanceScheme": "ODAGrants",
        },
    )

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(ctx)

    assert isinstance(result, BindResult)
    assert len(result.predicates) == 1
    pred = result.predicates[0]
    # Germany must be cleared; Africa is the recipient aggregate.
    assert pred.constraints["DevelopmentFinanceRecipient"] == "DAC/Africa"
    # Children in constraint_sets.
    assert pred.constraint_sets.get("DevelopmentFinanceRecipient") == frozenset(
        {"country/KEN", "country/TGO"}
    )


@pytest.mark.asyncio
async def test_set_binding_no_children_falls_back_to_scalar(
    crs_dac_candidates,
) -> None:
    """No children found → scalar parent path, constraint_sets == {}."""
    ctx = _crs_dac_ctx_with_places_and_contained_in(
        "malaria grants to african countries",
        (("DAC/Africa", "Africa", "african countries", "recipient"),),
        # parent_to_children empty for this parent — no expansion ran.
        {"DAC/Africa": ()},
        crs_dac_candidates,
    )
    mock = _make_generate_structured(
        0,
        {
            "DevelopmentFinancePurpose": "DAC/Malariacontrol",
            "DevelopmentFinanceRecipient": "DAC/Africa",
            "DevelopmentFinanceScheme": "ODAGrants",
        },
    )

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(ctx)

    assert isinstance(result, BindResult)
    assert len(result.predicates) == 1
    pred = result.predicates[0]
    assert pred.constraints["DevelopmentFinanceRecipient"] == "DAC/Africa"
    assert pred.constraint_sets == {}, "No children → scalar path, no set binding"


@pytest.mark.asyncio
async def test_set_binding_projection_composes_with_cross_product(
    crs_dac_candidates,
) -> None:
    """Recipient parent + 2-value purpose list → BOTH predicates carry projection.

    Used to silently collapse: when ``_explode_constraints`` yielded >1
    predicate, the recipient projection was dropped and the user got only
    scalar aggregates back. Now projection composes with cross-product:
    each of the N exploded predicates carries the same child set on the
    recipient slot, so "malaria and HIV grants to Nigerian sub-regions"
    fans out into (malaria × per-region) ∪ (HIV × per-region).
    """
    ctx = _crs_dac_ctx_with_places_and_contained_in(
        "malaria or HIV grants to Nigeria sub-regions",
        (
            ("country/NGA", "Nigeria", "Nigeria", "recipient"),
            ("country/NGA_Abia", "Abia", None, "ambiguous"),
            ("country/NGA_Lagos", "Lagos", None, "ambiguous"),
        ),
        {
            "country/NGA": (
                ("country/NGA_Abia", "Abia"),
                ("country/NGA_Lagos", "Lagos"),
            )
        },
        crs_dac_candidates,
    )
    # 2-element purpose list → cross-product yields 2 predicates.
    # country/* namespace is distinct from DAC/*, so the directional post-correction
    # for DevFinance shapes doesn't touch the purpose slot (no offerable places there).
    mock = _make_generate_structured(
        0,
        {
            "DevelopmentFinancePurpose": ["DAC/Malariacontrol", "DAC/STDcontrolincludingHIVAIDS"],
            "DevelopmentFinanceRecipient": "country/NGA",
            "DevelopmentFinanceScheme": "ODAGrants",
        },
    )

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(ctx)

    assert isinstance(result, BindResult)
    assert len(result.predicates) == 2, (
        "2-value purpose list must produce 2 predicates (cross-product)"
    )
    expected_children = frozenset({"country/NGA_Abia", "country/NGA_Lagos"})
    for pred in result.predicates:
        assert (
            pred.constraint_sets.get("DevelopmentFinanceRecipient") == expected_children
        ), "Projection must compose with cross-product, not collapse to scalar"


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


@pytest.mark.asyncio
async def test_set_binding_list_recipient_graceful_degrade(
    crs_dac_candidates,
) -> None:
    """LLM binds list[str] to recipient slot → no crash, constraint_sets == {}.

    isinstance guard catches list before membership test. Fails open to scalar.
    """
    ctx = _crs_dac_ctx_with_places_and_contained_in(
        "malaria grants to african countries",
        (
            ("DAC/Africa", "Africa", "african countries", "ambiguous"),
            ("country/KEN", "Kenya", None, "ambiguous"),
            ("country/TGO", "Togo", None, "ambiguous"),
        ),
        {"DAC/Africa": (("country/KEN", "Kenya"), ("country/TGO", "Togo"))},
        crs_dac_candidates,
    )
    # LLM returns list[str] for recipient slot.
    mock = _make_generate_structured(
        0,
        {
            "DevelopmentFinancePurpose": "DAC/Malariacontrol",
            "DevelopmentFinanceRecipient": ["DAC/Africa", "country/KEN"],
            "DevelopmentFinanceScheme": "ODAGrants",
        },
    )

    with patch("dc_search.slot_binding.llm.generate_structured", mock):
        result = await bind(ctx)

    assert isinstance(result, BindResult), "Must not crash with a 500"
    for pred in result.predicates:
        assert pred.constraint_sets == {}, (
            "Multi-value recipient binding must degrade to scalar aggregate "
            "(constraint_sets == {})"
        )


# ---------------------------------------------------------------------------
# decide_recipient_set_explosion: pin individual conditions
# ---------------------------------------------------------------------------


class TestDecideRecipientSetExplosion:
    """Direct unit tests for ``decide_recipient_set_explosion``.

    The decision used to be a multi-condition AND-chain scattered across
    ``bind()``; promoting it to a single named function lets each condition be
    pinned individually here. A regression that flips any of these conditions
    will land on a specific test instead of disappearing silently."""

    def _shape_context(
        self,
        *,
        query: str,
        crs_dac_candidates,
        resolved_places=(("DAC/Africa", "Africa", "african countries", "recipient"),),
        contained_in: bool = True,
        parent_to_children: dict | None = None,
    ):
        ptc = (
            parent_to_children
            if parent_to_children is not None
            else {"DAC/Africa": (("country/KEN", "Kenya"), ("country/TGO", "Togo"))}
        )
        return build_shape_context(
            query,
            crs_dac_candidates,
            resolved_places=resolved_places,
            contained_in=contained_in,
            parent_to_children=ptc,
        )

    def _devfinance_shape(self, shape_context):
        for s in shape_context.shapes:
            if s.population_type == "DevelopmentFinance":
                return s
        pytest.fail("no DevelopmentFinance shape in fixture")

    def test_fires_on_recipient_parent_with_children(self, crs_dac_candidates):
        from dc_search.slot_binding import decide_recipient_set_explosion

        ctx = self._shape_context(
            query="malaria grants to african countries",
            crs_dac_candidates=crs_dac_candidates,
        )
        shape = self._devfinance_shape(ctx)
        explosion = decide_recipient_set_explosion(
            chosen_shape=shape,
            shape_context=ctx,
            constraints={
                "DevelopmentFinancePurpose": "DAC/Malariacontrol",
                "DevelopmentFinanceRecipient": "DAC/Africa",
                "DevelopmentFinanceScheme": "ODAGrants",
            },
        )
        assert explosion.fired is True
        assert explosion.outer_conditions_met is True
        assert explosion.slot_to_children["DevelopmentFinanceRecipient"] == frozenset(
            {"country/KEN", "country/TGO"}
        )

    def test_skips_when_population_type_not_devfinance(self, crs_dac_candidates):
        from dc_search.slot_binding import decide_recipient_set_explosion

        ctx = self._shape_context(
            query="malaria grants to african countries",
            crs_dac_candidates=crs_dac_candidates,
        )
        shape = self._devfinance_shape(ctx)
        # Mock a non-DevFinance shape by passing population_type via dataclass replace.
        from dataclasses import replace

        non_devfin = replace(shape, population_type="Person")
        explosion = decide_recipient_set_explosion(
            chosen_shape=non_devfin,
            shape_context=ctx,
            constraints={},
        )
        assert explosion.fired is False
        assert explosion.outer_conditions_met is False
        # The failed condition is named in the trace.
        assert ("population_type_devfinance", False) in explosion.inner_trace

    def test_skips_when_contained_in_false(self, crs_dac_candidates):
        from dc_search.slot_binding import decide_recipient_set_explosion

        ctx = self._shape_context(
            query="malaria grants to Nigeria",
            crs_dac_candidates=crs_dac_candidates,
            resolved_places=(("country/NGA", "Nigeria", "Nigeria", "recipient"),),
            contained_in=False,
            parent_to_children={},
        )
        shape = self._devfinance_shape(ctx)
        explosion = decide_recipient_set_explosion(
            chosen_shape=shape,
            shape_context=ctx,
            constraints={"DevelopmentFinanceRecipient": "country/NGA"},
        )
        assert explosion.fired is False
        assert explosion.outer_conditions_met is False
        assert ("contained_in_detected", False) in explosion.inner_trace

    def test_skips_when_donor_role_parent(self, crs_dac_candidates):
        from dc_search.slot_binding import decide_recipient_set_explosion

        ctx = self._shape_context(
            query="grants from african countries",
            crs_dac_candidates=crs_dac_candidates,
            resolved_places=(("DAC/Africa", "Africa", "african countries", "donor"),),
        )
        shape = self._devfinance_shape(ctx)
        explosion = decide_recipient_set_explosion(
            chosen_shape=shape,
            shape_context=ctx,
            constraints={"DevelopmentFinanceRecipient": "DAC/Africa"},
        )
        assert explosion.fired is False
        assert explosion.outer_conditions_met is True
        # parent_role_is_recipient must be False — donor parent.
        assert ("parent_role_is_recipient", False) in explosion.inner_trace

    def test_skips_when_parent_is_list_valued(self, crs_dac_candidates):
        from dc_search.slot_binding import decide_recipient_set_explosion

        ctx = self._shape_context(
            query="malaria grants to africa and asia",
            crs_dac_candidates=crs_dac_candidates,
        )
        shape = self._devfinance_shape(ctx)
        explosion = decide_recipient_set_explosion(
            chosen_shape=shape,
            shape_context=ctx,
            constraints={
                "DevelopmentFinanceRecipient": ["DAC/Africa", "DAC/Asia"],
            },
        )
        assert explosion.fired is False
        assert explosion.outer_conditions_met is True
        # parent_bound_scalar must be False — list[str] doesn't pick a single parent.
        assert ("parent_bound_scalar", False) in explosion.inner_trace

    def test_skips_when_parent_unbound(self, crs_dac_candidates):
        from dc_search.slot_binding import decide_recipient_set_explosion

        ctx = self._shape_context(
            query="malaria grants to african countries",
            crs_dac_candidates=crs_dac_candidates,
        )
        shape = self._devfinance_shape(ctx)
        explosion = decide_recipient_set_explosion(
            chosen_shape=shape,
            shape_context=ctx,
            constraints={"DevelopmentFinanceRecipient": None},
        )
        assert explosion.fired is False
        assert ("parent_bound_scalar", False) in explosion.inner_trace


class TestLogRecipientSetNearMiss:
    """Near-miss telemetry: silent disappearance becomes a logged warning."""

    def test_no_log_when_explosion_fired(self, caplog, crs_dac_candidates):
        from dc_search.slot_binding import (
            RecipientSetExplosion,
            log_recipient_set_near_miss,
        )

        explosion = RecipientSetExplosion(
            slot_to_children={"DevelopmentFinanceRecipient": frozenset({"country/KEN"})},
            outer_conditions_met=True,
            inner_trace=(),
        )
        with caplog.at_level("WARNING", logger="dc_search.slot_binding"):
            log_recipient_set_near_miss(
                explosion, query="malaria grants to african countries"
            )
        assert "near-miss" not in caplog.text

    def test_no_log_when_outer_conditions_failed(self, caplog):
        from dc_search.slot_binding import (
            RecipientSetExplosion,
            log_recipient_set_near_miss,
        )

        explosion = RecipientSetExplosion(
            slot_to_children={},
            outer_conditions_met=False,
            inner_trace=(("population_type_devfinance", False),),
        )
        with caplog.at_level("WARNING", logger="dc_search.slot_binding"):
            log_recipient_set_near_miss(explosion, query="x")
        assert "near-miss" not in caplog.text

    def test_logs_when_outer_passed_but_inner_suppressed(self, caplog):
        from dc_search.slot_binding import (
            RecipientSetExplosion,
            log_recipient_set_near_miss,
        )

        explosion = RecipientSetExplosion(
            slot_to_children={},
            outer_conditions_met=True,
            inner_trace=(
                ("slot_offerable", True),
                ("parent_bound_scalar", True),
                ("parent_role_is_recipient", False),
                ("children_resolved", False),
            ),
        )
        with caplog.at_level("WARNING", logger="dc_search.slot_binding"):
            log_recipient_set_near_miss(
                explosion, query="grants from african countries"
            )
        assert "near-miss" in caplog.text
        assert "parent_role_is_recipient" in caplog.text
        assert "grants from african countries" in caplog.text


