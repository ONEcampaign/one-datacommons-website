"""Tests for the hook pipeline (hooks.py)."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from dc_search import retrieval
from dc_search.extraction import ExtractedDate
from dc_search.hooks import (
    DateFilterHook,
    DenominatorImplicitHook,
    HookContext,
    PlaceAvailabilityHook,
    RetrievalQualityHook,
    SetCapHook,
    WeakRetrievalTopicDumpHook,
    materialize_many,
    materialize_via_hooks,
)
from dc_search.predicate import AnswerCollection, AskClarification, Caveat, Confidence, Predicate
from dc_search.retrieval import StatVarFeatures, VariableGroupInfo

# ---------------------------------------------------------------------------
# Fixtures — minimal candidates
# ---------------------------------------------------------------------------

_CRS_CANDIDATES = [
    StatVarFeatures(
        dcid="ONE/CRS_DAC/Malariacontrol-ODAGrants-KEN",
        name="Malaria grants to Kenya",
        population_type=["DevelopmentFinance"],
        measured_property=["DevelopmentFinanceFlow"],
        stat_type=["measuredValue"],
        constraints={
            "DevelopmentFinancePurpose": ["DAC/Malariacontrol"],
            "DevelopmentFinanceRecipient": ["country/KEN"],
            "DevelopmentFinanceScheme": ["ODAGrants"],
        },
    ),
]

_CENSUS_CANDIDATES = [
    StatVarFeatures(
        dcid="Count_Person",
        name="Total Population",
        population_type=["Person"],
        measured_property=["count"],
        stat_type=["measuredValue"],
    ),
    StatVarFeatures(
        dcid="Count_Person_Female",
        name="Female Population",
        population_type=["Person"],
        measured_property=["count"],
        stat_type=["measuredValue"],
        constraints={"gender": ["Female"]},
    ),
]

_WHO_CANDIDATES = [
    StatVarFeatures(
        dcid="ONE/who_dis13",
        name="Malaria incidence",
        population_type=["Thing"],
        measured_property=["who/dis13"],
        stat_type=["measuredValue"],
    ),
]

_EMPTY_VG = VariableGroupInfo(
    dcid="ONE/g/DevelopmentFinance_test",
    name="Test",
    parents=[],
    child_groups=[],
    child_vars=[{"dcid": "ONE/CRS_DAC/Malariacontrol-ODAGrants-KEN", "name": "test"}],
)

# Truly empty group — no child_vars and no child_groups → svg_verified=False.
_UNVERIFIED_VG = VariableGroupInfo(
    dcid="ONE/g/DevelopmentFinance_test",
    name="Test",
    parents=[],
    child_groups=[],
    child_vars=[],
)

# Real group the import wired onto the SV via memberOf; differs from whatever
# _build_crs_svg_dcid synthesizes, so it exercises the drift / recovery paths.
# The leading dc/g entry is an unrelated rollup group and must be ignored.
_CRS_CANDIDATES_WITH_MEMBEROF = [
    StatVarFeatures(
        dcid="ONE/CRS_DAC/Malariacontrol-ODAGrants-KEN",
        name="Malaria grants to Kenya",
        population_type=["DevelopmentFinance"],
        measured_property=["DevelopmentFinanceFlow"],
        stat_type=["measuredValue"],
        constraints={
            "DevelopmentFinancePurpose": ["DAC/Malariacontrol"],
            "DevelopmentFinanceRecipient": ["country/KEN"],
            "DevelopmentFinanceScheme": ["ODAGrants"],
        },
        member_of=[
            "dc/g/Some_Topic_Rollup",
            "ONE/g/DevelopmentFinance_RealMalariaGroup",
        ],
    ),
]


def _make_ctx(
    candidates: list[StatVarFeatures],
    place_availability: frozenset[str] | None = None,
) -> HookContext:
    return HookContext(
        place_dcids=(),
        place_availability=place_availability,
        retrieval_scores={},
        raw_candidates=tuple(candidates),
    )


# ---------------------------------------------------------------------------
# Test 1: CRS_DAC via hook pipeline — CrsDacSvgExpansionHook fires
# ---------------------------------------------------------------------------


def test_materialize_via_hooks_crs_dac() -> None:
    """materialize_via_hooks with CRS_DAC predicate produces AnswerCollection
    with svg_dcid set."""
    predicate = Predicate(
        population_type="DevelopmentFinance",
        measured_property="DevelopmentFinanceFlow",
        constraints={
            "DevelopmentFinancePurpose": "DAC/Malariacontrol",
            "DevelopmentFinanceRecipient": "country/KEN",
            "DevelopmentFinanceScheme": "ODAGrants",
        },
    )
    ctx = _make_ctx(_CRS_CANDIDATES)

    with patch("dc_search.hooks.variable_group", return_value=_EMPTY_VG):
        result = materialize_via_hooks(predicate, _CRS_CANDIDATES, ctx=ctx)

    assert isinstance(result, AnswerCollection)
    assert "ONE/CRS_DAC/Malariacontrol-ODAGrants-KEN" in result.sv_set
    assert result.svg_dcids
    assert any("DevelopmentFinance" in d for d in result.svg_dcids)


_CRS_FULLY_BOUND_PREDICATE = Predicate(
    population_type="DevelopmentFinance",
    measured_property="DevelopmentFinanceFlow",
    constraints={
        "DevelopmentFinancePurpose": "DAC/Malariacontrol",
        "DevelopmentFinanceRecipient": "country/KEN",
        "DevelopmentFinanceScheme": "ODAGrants",
    },
)


def test_crs_dac_synthesis_drift_warns(caplog) -> None:
    """Synthesized DCID resolves but disagrees with candidate memberOf → warn.

    Happy path is preserved (confidence still high); the mismatch is logged so
    naming-recipe drift surfaces in telemetry instead of as silent recall loss.
    """
    ctx = _make_ctx(_CRS_CANDIDATES_WITH_MEMBEROF)
    with (
        patch("dc_search.hooks.variable_group", return_value=_EMPTY_VG),
        caplog.at_level(logging.WARNING, logger="dc_search.hooks"),
    ):
        result = materialize_via_hooks(
            _CRS_FULLY_BOUND_PREDICATE, _CRS_CANDIDATES_WITH_MEMBEROF, ctx=ctx
        )

    assert isinstance(result, AnswerCollection)
    assert result.confidence == "high"
    assert any("drift" in r.message for r in caplog.records)


def test_crs_dac_synthesis_failure_recovers_via_member_of(caplog) -> None:
    """Fully-bound predicate: synthesis fails → recover the group from memberOf.

    The real group is read off the candidate (ignoring the unrelated dc/g
    rollup), the verified/high-confidence signal is recovered, and no
    retrieval_weak caveat is emitted.
    """
    ctx = _make_ctx(_CRS_CANDIDATES_WITH_MEMBEROF)
    with (
        patch("dc_search.hooks.variable_group", return_value=_UNVERIFIED_VG),
        caplog.at_level(logging.WARNING, logger="dc_search.hooks"),
    ):
        result = materialize_via_hooks(
            _CRS_FULLY_BOUND_PREDICATE, _CRS_CANDIDATES_WITH_MEMBEROF, ctx=ctx
        )

    assert isinstance(result, AnswerCollection)
    assert result.confidence == "high"
    assert result.svg_dcids == ("ONE/g/DevelopmentFinance_RealMalariaGroup",)
    assert "retrieval_weak" not in result.caveats
    assert any("recovered via candidate memberOf" in r.message for r in caplog.records)


def test_crs_dac_wildcard_synthesis_failure_degrades(caplog) -> None:
    """Wildcard predicate: synthesis fails → degrade as before, but log loudly.

    No memberOf recovery (the leaf group is the wrong granularity for a
    wildcard), so retrieval_weak is added and confidence stays medium.
    """
    predicate = Predicate(
        population_type="DevelopmentFinance",
        measured_property="DevelopmentFinanceFlow",
        constraints={
            "DevelopmentFinancePurpose": "DAC/Malariacontrol",
            "DevelopmentFinanceRecipient": "country/KEN",
            "DevelopmentFinanceScheme": None,
        },
    )
    ctx = _make_ctx(_CRS_CANDIDATES_WITH_MEMBEROF)
    with (
        patch("dc_search.hooks.variable_group", return_value=_UNVERIFIED_VG),
        caplog.at_level(logging.WARNING, logger="dc_search.hooks"),
    ):
        result = materialize_via_hooks(predicate, _CRS_CANDIDATES_WITH_MEMBEROF, ctx=ctx)

    assert isinstance(result, AnswerCollection)
    assert "retrieval_weak" in result.caveats
    assert result.confidence == "medium"
    assert any("degrading" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Test 2: Census via hook pipeline — DenominatorImplicitHook fires
# ---------------------------------------------------------------------------


def test_materialize_via_hooks_census() -> None:
    """materialize_via_hooks with Census Person/count predicate filters correctly
    and adds denominator_implicit caveat."""
    predicate = Predicate(
        population_type="Person",
        measured_property="count",
        constraints={"gender": "Female"},
    )
    ctx = _make_ctx(_CENSUS_CANDIDATES)

    result = materialize_via_hooks(predicate, _CENSUS_CANDIDATES, ctx=ctx)

    assert isinstance(result, AnswerCollection)
    assert "Count_Person_Female" in result.sv_set
    assert "Count_Person" not in result.sv_set


# ---------------------------------------------------------------------------
# materialize_via_hooks — filtering_degraded caveat (fail-open signal)
# ---------------------------------------------------------------------------


def _degraded_test_predicate() -> Predicate:
    return Predicate(
        population_type="Person", measured_property="count", constraints={"gender": "Female"}
    )


def test_materialize_via_hooks_filtering_degraded_on_in_hook_failure() -> None:
    """An in-hook coverage/date fetch that fails open trips the ContextVar, which
    materialize_via_hooks turns into a filtering_degraded caveat."""
    ctx = _make_ctx(_CENSUS_CANDIDATES)
    with patch("dc_search.hooks.dc_call_was_degraded", return_value=True):
        result = materialize_via_hooks(_degraded_test_predicate(), _CENSUS_CANDIDATES, ctx=ctx)
    assert isinstance(result, AnswerCollection)
    assert "filtering_degraded" in result.caveats


def test_materialize_via_hooks_filtering_degraded_on_availability_degraded() -> None:
    """A degraded availability re-rank (captured on HookContext) surfaces the caveat."""
    ctx = HookContext(
        place_dcids=(),
        place_availability=None,
        retrieval_scores={},
        raw_candidates=tuple(_CENSUS_CANDIDATES),
        availability_degraded=True,
    )
    result = materialize_via_hooks(_degraded_test_predicate(), _CENSUS_CANDIDATES, ctx=ctx)
    assert isinstance(result, AnswerCollection)
    assert "filtering_degraded" in result.caveats


def test_materialize_via_hooks_no_degraded_caveat_when_clean() -> None:
    """No transient failure → no filtering_degraded caveat."""
    ctx = _make_ctx(_CENSUS_CANDIDATES)
    result = materialize_via_hooks(_degraded_test_predicate(), _CENSUS_CANDIDATES, ctx=ctx)
    assert isinstance(result, AnswerCollection)
    assert "filtering_degraded" not in result.caveats


# ---------------------------------------------------------------------------
# Test 3: WHO via hook pipeline — data-driven confidence
# ---------------------------------------------------------------------------


def test_materialize_via_hooks_who_data_driven_confidence() -> None:
    """WHO predicate: confidence is data-driven. Without retrieval scores,
    RetrievalQualityHook is a no-op → medium confidence."""
    predicate = Predicate(
        population_type=None,
        measured_property=None,
        constraints={},
    )
    ctx = _make_ctx(_WHO_CANDIDATES)

    result = materialize_via_hooks(predicate, _WHO_CANDIDATES, ctx=ctx)

    assert isinstance(result, AnswerCollection)
    # No retrieval scores provided → RetrievalQualityHook is no-op → medium
    assert result.confidence == "medium"


# ---------------------------------------------------------------------------
# Test 4: AskClarification short-circuits remaining hooks
# ---------------------------------------------------------------------------


def test_ask_clarification_short_circuits_chain() -> None:
    """When a hook returns AskClarification, remaining hooks are not called."""
    predicate = Predicate(
        population_type="DevelopmentFinance",
        measured_property="DevelopmentFinanceFlow",
        constraints={
            "DevelopmentFinancePurpose": "DAC/NonExistent",
            "DevelopmentFinanceRecipient": "country/ZZZ",
            "DevelopmentFinanceScheme": "ODAGrants",
        },
    )
    ctx = _make_ctx(_CRS_CANDIDATES)

    result = materialize_via_hooks(predicate, _CRS_CANDIDATES, ctx=ctx)

    assert isinstance(result, AskClarification)
    assert result.reason == "under_specified"


# ---------------------------------------------------------------------------
# Test 5: Unknown namespace with candidates → AnswerCollection(medium)
# ---------------------------------------------------------------------------


def test_materialize_via_hooks_unknown_namespace() -> None:
    """Unknown namespace with unconstrained predicate → AnswerCollection(medium)."""
    predicate = Predicate(
        population_type=None,
        measured_property=None,
        constraints={},
    )
    ctx = _make_ctx(_CENSUS_CANDIDATES)

    result = materialize_via_hooks(predicate, _CENSUS_CANDIDATES, ctx=ctx)

    assert isinstance(result, AnswerCollection)
    assert result.confidence == "medium"


# ===========================================================================
# Shared hook tests
# ===========================================================================


def _census_predicate(**kwargs) -> Predicate:  # type: ignore[no-untyped-def]
    return Predicate(
        population_type="Person",
        measured_property="count",
        constraints=kwargs.get("constraints", {}),
    )


def _answer(
    sv_set: list[str], caveats: list[Caveat] | None = None, confidence: Confidence = "medium"
) -> AnswerCollection:
    return AnswerCollection(
        predicate=_census_predicate(),
        sv_set=sv_set,
        confidence=confidence,
        caveats=caveats or [],
    )


# ---------------------------------------------------------------------------
# SetCapHook
# ---------------------------------------------------------------------------


def test_set_cap_hook_fires_at_threshold() -> None:
    """SetCapHook adds set_valued_answer when sv_set length >= threshold."""
    hook = SetCapHook()
    pred = _census_predicate()
    result = _answer(["SV1", "SV2", "SV3", "SV4", "SV5"])
    ctx = _make_ctx([])

    out = hook.run(pred, result, ctx)

    assert isinstance(out, AnswerCollection)
    assert "set_valued_answer" in out.caveats


def test_set_cap_hook_no_fire_below_threshold() -> None:
    """SetCapHook does not fire when sv_set length < threshold."""
    hook = SetCapHook()
    pred = _census_predicate()
    result = _answer(["SV1", "SV2", "SV3", "SV4"])  # 4 < 5
    ctx = _make_ctx([])

    out = hook.run(pred, result, ctx)

    assert isinstance(out, AnswerCollection)
    assert "set_valued_answer" not in out.caveats


def test_set_cap_hook_idempotent() -> None:
    """SetCapHook does not duplicate set_valued_answer if already present."""
    hook = SetCapHook()
    pred = _census_predicate()
    result = _answer(["SV1", "SV2", "SV3", "SV4", "SV5"], caveats=["set_valued_answer"])
    ctx = _make_ctx([])

    out = hook.run(pred, result, ctx)

    assert isinstance(out, AnswerCollection)
    assert out.caveats.count("set_valued_answer") == 1


# ---------------------------------------------------------------------------
# PlaceAvailabilityHook
# ---------------------------------------------------------------------------


def test_place_availability_hook_filters_sv_set() -> None:
    """PlaceAvailabilityHook removes SVs not in place_availability."""
    hook = PlaceAvailabilityHook()
    pred = _census_predicate()
    result = _answer(["SV_A", "SV_B", "SV_C"])
    avail = frozenset({"SV_A", "SV_C"})
    ctx = _make_ctx([], place_availability=avail)

    out = hook.run(pred, result, ctx)

    assert isinstance(out, AnswerCollection)
    assert out.sv_set == ["SV_A", "SV_C"]
    assert "availability_filtered" in out.caveats


def test_place_availability_hook_no_op_when_all_available() -> None:
    """PlaceAvailabilityHook is a no-op when all SVs are in availability."""
    hook = PlaceAvailabilityHook()
    pred = _census_predicate()
    result = _answer(["SV_A", "SV_B"])
    avail = frozenset({"SV_A", "SV_B", "SV_C"})
    ctx = _make_ctx([], place_availability=avail)

    out = hook.run(pred, result, ctx)

    assert out is result  # exact same object — no copy made


def test_place_availability_hook_does_not_apply_when_none() -> None:
    """PlaceAvailabilityHook.applies() returns False when place_availability is None."""
    hook = PlaceAvailabilityHook()
    pred = _census_predicate()
    ctx = _make_ctx([], place_availability=None)

    assert hook.applies(pred, (), ctx) is False


# ---------------------------------------------------------------------------
# DenominatorImplicitHook
# ---------------------------------------------------------------------------


def test_denominator_implicit_hook_fires_for_person_count() -> None:
    """DenominatorImplicitHook fires for Person/count without measurementDenominator."""
    hook = DenominatorImplicitHook()
    pred = _census_predicate()  # Person/count, no measurementDenominator
    result = _answer(["Count_Person"])
    ctx = _make_ctx([])

    assert hook.applies(pred, (), ctx) is True
    out = hook.run(pred, result, ctx)

    assert isinstance(out, AnswerCollection)
    assert "denominator_implicit" in out.caveats


def test_denominator_implicit_hook_no_fire_when_bound() -> None:
    """DenominatorImplicitHook does not fire when measurementDenominator is present."""
    hook = DenominatorImplicitHook()
    pred = Predicate(
        population_type="Person",
        measured_property="count",
        constraints={"measurementDenominator": "Count_Person"},
    )
    _answer(["Count_Person_Female_AsAFractionOf_Count_Person"])
    ctx = _make_ctx([])

    assert hook.applies(pred, (), ctx) is False


def test_denominator_implicit_hook_idempotent() -> None:
    """DenominatorImplicitHook does not duplicate caveat if already present."""
    hook = DenominatorImplicitHook()
    pred = _census_predicate()
    result = _answer(["Count_Person"], caveats=["denominator_implicit"])
    ctx = _make_ctx([])

    out = hook.run(pred, result, ctx)

    assert isinstance(out, AnswerCollection)
    assert out.caveats.count("denominator_implicit") == 1


# ---------------------------------------------------------------------------
# RetrievalQualityHook
# ---------------------------------------------------------------------------


def test_retrieval_quality_hook_downgrades_weak_score() -> None:
    """RetrievalQualityHook sets confidence=low and adds retrieval_weak when
    max score is below _RETRIEVAL_QUALITY_THRESHOLD."""
    hook = RetrievalQualityHook()
    pred = _census_predicate()
    result = _answer(["SV_WHO"], confidence="medium")
    ctx = HookContext(
        place_dcids=(),
        place_availability=None,
        retrieval_scores={"SV_WHO": 0.3},
        raw_candidates=(),
    )

    assert hook.applies(pred, (), ctx) is True
    out = hook.run(pred, result, ctx)

    assert isinstance(out, AnswerCollection)
    assert out.confidence == "low"
    assert "retrieval_weak" in out.caveats


def test_retrieval_quality_hook_no_downgrade_strong_score() -> None:
    """RetrievalQualityHook leaves confidence unchanged for strong scores."""
    hook = RetrievalQualityHook()
    pred = _census_predicate()
    result = _answer(["Count_Person"], confidence="high")
    ctx = HookContext(
        place_dcids=(),
        place_availability=None,
        retrieval_scores={"Count_Person": 0.85},
        raw_candidates=(),
    )

    out = hook.run(pred, result, ctx)

    assert isinstance(out, AnswerCollection)
    assert out.confidence == "high"
    assert "retrieval_weak" not in out.caveats


def test_retrieval_quality_hook_no_op_empty_scores() -> None:
    """RetrievalQualityHook.applies() returns False when retrieval_scores is empty."""
    hook = RetrievalQualityHook()
    pred = _census_predicate()
    ctx = _make_ctx([])  # retrieval_scores={}

    assert hook.applies(pred, (), ctx) is False


def test_retrieval_quality_hook_idempotent_retrieval_weak() -> None:
    """RetrievalQualityHook does not duplicate retrieval_weak if already present."""
    hook = RetrievalQualityHook()
    pred = _census_predicate()
    result = _answer(["SV_WHO"], confidence="low", caveats=["retrieval_weak"])
    ctx = HookContext(
        place_dcids=(),
        place_availability=None,
        retrieval_scores={"SV_WHO": 0.2},
        raw_candidates=(),
    )

    out = hook.run(pred, result, ctx)

    assert isinstance(out, AnswerCollection)
    assert out.caveats.count("retrieval_weak") == 1


# ===========================================================================
# materialize_many tests
# ===========================================================================

_KEN_SV = "ONE/CRS_DAC/COVID19control-ODAGrants-KEN"
_TGO_SV = "ONE/CRS_DAC/COVID19control-ODAGrants-TGO"

_MULTI_CANDIDATES = [
    StatVarFeatures(
        dcid=_KEN_SV,
        name="COVID grants to Kenya",
        population_type=["DevelopmentFinance"],
        measured_property=["DevelopmentFinanceFlow"],
        stat_type=["measuredValue"],
        constraints={
            "DevelopmentFinancePurpose": ["DAC/COVID19control"],
            "DevelopmentFinanceRecipient": ["country/KEN"],
            "DevelopmentFinanceScheme": ["ODAGrants"],
        },
    ),
    StatVarFeatures(
        dcid=_TGO_SV,
        name="COVID grants to Togo",
        population_type=["DevelopmentFinance"],
        measured_property=["DevelopmentFinanceFlow"],
        stat_type=["measuredValue"],
        constraints={
            "DevelopmentFinancePurpose": ["DAC/COVID19control"],
            "DevelopmentFinanceRecipient": ["country/TGO"],
            "DevelopmentFinanceScheme": ["ODAGrants"],
        },
    ),
]

_KEN_VG = VariableGroupInfo(
    dcid="ONE/g/DevelopmentFinance_DAC-COVID19control_CountryKEN_ODAGrants",
    name="COVID Kenya",
    parents=[],
    child_groups=[],
    child_vars=[{"dcid": _KEN_SV, "name": "COVID19 grants KEN"}],
)

_TGO_VG = VariableGroupInfo(
    dcid="ONE/g/DevelopmentFinance_DAC-COVID19control_CountryTGO_ODAGrants",
    name="COVID Togo",
    parents=[],
    child_groups=[],
    child_vars=[{"dcid": _TGO_SV, "name": "COVID19 grants TGO"}],
)


def _ken_predicate() -> Predicate:
    return Predicate(
        population_type="DevelopmentFinance",
        measured_property="DevelopmentFinanceFlow",
        constraints={
            "DevelopmentFinancePurpose": "DAC/COVID19control",
            "DevelopmentFinanceRecipient": "country/KEN",
            "DevelopmentFinanceScheme": "ODAGrants",
        },
    )


def _tgo_predicate() -> Predicate:
    return Predicate(
        population_type="DevelopmentFinance",
        measured_property="DevelopmentFinanceFlow",
        constraints={
            "DevelopmentFinancePurpose": "DAC/COVID19control",
            "DevelopmentFinanceRecipient": "country/TGO",
            "DevelopmentFinanceScheme": "ODAGrants",
        },
    )


def test_materialize_many_single_predicate_unchanged() -> None:
    """1-tuple input: output is byte-for-byte equivalent to materialize_via_hooks."""
    predicate = _ken_predicate()
    ctx = _make_ctx(_MULTI_CANDIDATES)

    with patch("dc_search.hooks.variable_group", return_value=_KEN_VG):
        expected = materialize_via_hooks(predicate, _MULTI_CANDIDATES, ctx=ctx)
        result = materialize_many((predicate,), _MULTI_CANDIDATES, ctx=ctx)

    assert isinstance(result, AnswerCollection)
    assert isinstance(expected, AnswerCollection)
    assert result.sv_set == expected.sv_set
    assert result.svg_dcids == expected.svg_dcids
    assert result.caveats == expected.caveats
    assert result.confidence == expected.confidence


def test_materialize_many_unions_two_recipients() -> None:
    """Kenya + Togo sub-predicates: both SVs in sv_set, both SVGs in svg_dcids."""
    predicates = (_ken_predicate(), _tgo_predicate())
    ctx = HookContext(
        place_dcids=(),
        place_availability=None,
        retrieval_scores={_KEN_SV: 0.9, _TGO_SV: 0.7},
        raw_candidates=tuple(_MULTI_CANDIDATES),
    )

    def _vg_side_effect(*, dcid: str) -> VariableGroupInfo:
        if "KEN" in dcid:
            return _KEN_VG
        return _TGO_VG

    with patch("dc_search.hooks.variable_group", side_effect=_vg_side_effect):
        result = materialize_many(predicates, _MULTI_CANDIDATES, ctx=ctx)

    assert isinstance(result, AnswerCollection)
    assert _KEN_SV in result.sv_set
    assert _TGO_SV in result.sv_set
    assert result.sv_set.index(_KEN_SV) < result.sv_set.index(_TGO_SV)
    assert len(result.svg_dcids) == 2


def test_materialize_many_mixed_clarification_retrieval_weak() -> None:
    """One sub-predicate returns retrieval_weak → AnswerCollection with partial_result."""
    ken_pred = _ken_predicate()
    empty_census_pred = Predicate(
        population_type="Person",
        measured_property="count",
        constraints={"gender": "NonExistentGender"},
    )
    ken_only = [_MULTI_CANDIDATES[0]]
    ctx = _make_ctx(ken_only)

    with patch("dc_search.hooks.variable_group", return_value=_KEN_VG):
        result = materialize_many((ken_pred, empty_census_pred), ken_only, ctx=ctx)

    assert isinstance(result, AnswerCollection)
    assert _KEN_SV in result.sv_set
    assert "partial_result" in result.caveats


def test_materialize_many_mixed_clarification_under_specified() -> None:
    """One sub-predicate returns under_specified → AskClarification poisons union."""
    ken_pred = _ken_predicate()
    bad_pred = Predicate(
        population_type="DevelopmentFinance",
        measured_property="DevelopmentFinanceFlow",
        constraints={
            "DevelopmentFinancePurpose": "DAC/NonExistent",
            "DevelopmentFinanceRecipient": "country/ZZZ",
            "DevelopmentFinanceScheme": "ODAGrants",
        },
    )
    ctx = _make_ctx(_MULTI_CANDIDATES)

    with patch("dc_search.hooks.variable_group", return_value=_KEN_VG):
        result = materialize_many((ken_pred, bad_pred), _MULTI_CANDIDATES, ctx=ctx)

    assert isinstance(result, AskClarification)
    assert result.reason == "under_specified"


def test_materialize_many_caveat_dedup() -> None:
    """Caveats shared across sub-predicates appear exactly once in merged result."""
    ken_pred = _ken_predicate()
    tgo_pred = _tgo_predicate()
    ctx = _make_ctx(_MULTI_CANDIDATES)

    def _vg_side_effect(*, dcid: str) -> VariableGroupInfo:
        if "KEN" in dcid:
            return _KEN_VG
        return _TGO_VG

    with patch("dc_search.hooks.variable_group", side_effect=_vg_side_effect):
        result = materialize_many((ken_pred, tgo_pred), _MULTI_CANDIDATES, ctx=ctx)

    assert isinstance(result, AnswerCollection)
    caveat_counts = {c: result.caveats.count(c) for c in set(result.caveats)}
    for caveat, count in caveat_counts.items():
        assert count == 1, f"Caveat {caveat!r} appears {count} times (expected 1)"


# ---------------------------------------------------------------------------
# PlaceAvailabilityHook skip rule tests
# ---------------------------------------------------------------------------


def test_place_availability_hook_skip_when_place_bound() -> None:
    """PlaceAvailabilityHook.applies returns False when ctx.place_dcids are all
    bound as constraint values on the predicate."""
    hook = PlaceAvailabilityHook()
    predicate = Predicate(
        population_type="DevelopmentFinance",
        measured_property="DevelopmentFinanceFlow",
        constraints={
            "DevelopmentFinanceRecipient": "country/KEN",
            "DevelopmentFinancePurpose": "DAC/COVID19control",
        },
    )
    ctx = HookContext(
        place_dcids=("country/KEN",),
        place_availability=frozenset({"ONE/CRS_DAC/COVID19control-ODAGrants-KEN"}),
        retrieval_scores={},
        raw_candidates=(),
    )

    assert hook.applies(predicate, (), ctx) is False


def test_place_availability_hook_runs_when_place_unbound() -> None:
    """PlaceAvailabilityHook.applies returns True when ctx.place_dcids has a
    place not appearing in any constraint value."""
    hook = PlaceAvailabilityHook()
    predicate = Predicate(
        population_type="Person",
        measured_property="count",
        constraints={"gender": "Female"},
    )
    ctx = HookContext(
        place_dcids=("country/GTM",),
        place_availability=frozenset({"Count_Person_Female"}),
        retrieval_scores={},
        raw_candidates=(),
    )

    assert hook.applies(predicate, (), ctx) is True


# ===========================================================================
# materialize_many pre-warm of variable_groups_batch
# ===========================================================================

_BATCH_RECIPIENTS = ("KEN", "TGO", "AGO", "MOZ")
_BATCH_SVS = {r: f"ONE/CRS_DAC/Malariacontrol-ODAGrants-{r}" for r in _BATCH_RECIPIENTS}
_BATCH_SVG_DCIDS = {
    r: (
        "ONE/g/DevelopmentFinance"
        "_DevelopmentFinancePurpose-DACMalariacontrol"
        f"_DevelopmentFinanceRecipient-Country{r}"
        "_DevelopmentFinanceScheme-ODAGrants"
    )
    for r in _BATCH_RECIPIENTS
}


def _make_batch_predicate(recipient: str) -> Predicate:
    return Predicate(
        population_type="DevelopmentFinance",
        measured_property="DevelopmentFinanceFlow",
        constraints={
            "DevelopmentFinancePurpose": "DAC/Malariacontrol",
            "DevelopmentFinanceRecipient": f"country/{recipient}",
            "DevelopmentFinanceScheme": "ODAGrants",
        },
    )


def _make_batch_candidate(recipient: str) -> StatVarFeatures:
    return StatVarFeatures(
        dcid=_BATCH_SVS[recipient],
        name=f"Malaria grants to {recipient}",
        population_type=["DevelopmentFinance"],
        measured_property=["DevelopmentFinanceFlow"],
        stat_type=["measuredValue"],
        constraints={
            "DevelopmentFinancePurpose": ["DAC/Malariacontrol"],
            "DevelopmentFinanceRecipient": [f"country/{recipient}"],
            "DevelopmentFinanceScheme": ["ODAGrants"],
        },
    )


def _vgroup_out_resp(dcids: list[str]) -> dict[str, object]:
    return {
        "data": {
            dcid: {
                "arcs": {
                    "name": {"nodes": [{"value": f"Group {dcid[-3:]}"}]},
                    "specializationOf": {
                        "nodes": [{"dcid": "ONE/g/DevelopmentFinance", "name": "Root"}]
                    },
                }
            }
            for dcid in dcids
        }
    }


def _vgroup_in_resp_with_svs(dcid_to_sv: dict[str, str]) -> dict[str, object]:
    return {
        "data": {
            dcid: {
                "arcs": {
                    "specializationOf": {"nodes": []},
                    "memberOf": {"nodes": [{"dcid": sv_dcid, "name": sv_dcid}]},
                }
            }
            for dcid, sv_dcid in dcid_to_sv.items()
        }
    }


def _to_mock_result(raw: dict[str, object]) -> MagicMock:
    mock = MagicMock()
    mock.to_dict.return_value = raw
    return mock


def test_materialize_many_prewarms_variable_groups_batch() -> None:
    """4 CRS_DAC sub-predicates trigger exactly 2 client.node.fetch calls (the
    batch pre-warm), not 8 (2 per sub-predicate x 4 if each called variable_group
    cold)."""
    retrieval._vgroups_cache.clear()

    predicates = tuple(_make_batch_predicate(r) for r in _BATCH_RECIPIENTS)
    all_candidates = [_make_batch_candidate(r) for r in _BATCH_RECIPIENTS]

    svg_dcids: list[str] = [_BATCH_SVG_DCIDS[r] for r in _BATCH_RECIPIENTS]
    dcid_to_sv: dict[str, str] = {_BATCH_SVG_DCIDS[r]: _BATCH_SVS[r] for r in _BATCH_RECIPIENTS}

    ctx = _make_ctx(all_candidates)

    with patch("dc_search.retrieval.get_client") as mock_get_client:
        client = MagicMock()
        mock_get_client.return_value = client
        client.node.fetch.side_effect = [
            _to_mock_result(_vgroup_out_resp(svg_dcids)),
            _to_mock_result(_vgroup_in_resp_with_svs(dcid_to_sv)),
        ]

        result = materialize_many(predicates, all_candidates, ctx=ctx)

    assert client.node.fetch.call_count == 2, (
        f"Expected 2 fetch calls (pre-warm batch); got {client.node.fetch.call_count}. "
        "Without pre-warm there would be 8 calls (2 per sub-predicate x 4)."
    )

    assert isinstance(result, AnswerCollection)
    for r in _BATCH_RECIPIENTS:
        assert _BATCH_SVS[r] in result.sv_set, f"Expected {_BATCH_SVS[r]} in sv_set"


def test_materialize_many_single_predicate_no_prewarm() -> None:
    """1-tuple input does NOT trigger variable_groups_batch (pre-warm skipped)."""
    predicate = _make_batch_predicate("KEN")
    candidates = [_make_batch_candidate("KEN")]
    ctx = _make_ctx(candidates)

    with (
        patch("dc_search.hooks.variable_groups_batch") as mock_batch,
        patch("dc_search.hooks.variable_group", return_value=_EMPTY_VG),
    ):
        result = materialize_many((predicate,), candidates, ctx=ctx)

    mock_batch.assert_not_called()
    assert isinstance(result, AnswerCollection)


# ===========================================================================
# WeakRetrievalTopicDumpHook tests
# ===========================================================================


def _topic_answer(sv_set: list[str], caveats: list[Caveat] | None = None) -> AnswerCollection:
    return AnswerCollection(
        predicate=_census_predicate(),
        sv_set=sv_set,
        confidence="high",
        caveats=caveats or ["topic_expanded"],
    )


def test_weak_retrieval_topic_dump_hook_fires() -> None:
    """Hook fires when topic_expanded in caveats, place_dcids non-empty, top score < 0.72."""
    hook = WeakRetrievalTopicDumpHook()
    pred = _census_predicate()
    result = _topic_answer(["dc/SomeTopic_SV1", "dc/SomeTopic_SV2"])
    ctx = HookContext(
        place_dcids=("country/TGO",),
        place_availability=None,
        retrieval_scores={"dc/SomeTopic_SV1": 0.65, "dc/SomeTopic_SV2": 0.60},
        raw_candidates=(),
    )

    assert hook.applies(pred, (), ctx) is True
    out = hook.run(pred, result, ctx)

    assert isinstance(out, AskClarification)
    assert out.reason == "retrieval_weak"


def test_weak_retrieval_topic_dump_hook_no_fire_above_threshold() -> None:
    """Hook does not fire when max retrieval score is >= 0.72."""
    hook = WeakRetrievalTopicDumpHook()
    pred = _census_predicate()
    result = _topic_answer(["dc/TbIncidence_SV"])
    ctx = HookContext(
        place_dcids=("country/KEN",),
        place_availability=None,
        retrieval_scores={"dc/TbIncidence_SV": 0.85},
        raw_candidates=(),
    )

    out = hook.run(pred, result, ctx)

    assert out is result


def test_weak_retrieval_topic_dump_hook_no_fire_without_topic_expanded() -> None:
    """Hook does not fire when 'topic_expanded' is absent from caveats."""
    hook = WeakRetrievalTopicDumpHook()
    pred = _census_predicate()
    result = _topic_answer(["dc/SomeSV"], caveats=["retrieval_weak"])
    ctx = HookContext(
        place_dcids=("country/KEN",),
        place_availability=None,
        retrieval_scores={"dc/SomeSV": 0.40},
        raw_candidates=(),
    )

    out = hook.run(pred, result, ctx)

    assert out is result


def test_weak_retrieval_topic_dump_hook_no_fire_empty_place_dcids() -> None:
    """Hook.applies() returns False when ctx.place_dcids is empty."""
    hook = WeakRetrievalTopicDumpHook()
    pred = _census_predicate()
    ctx = HookContext(
        place_dcids=(),
        place_availability=None,
        retrieval_scores={"dc/SomeSV": 0.40},
        raw_candidates=(),
    )

    assert hook.applies(pred, (), ctx) is False


# ===========================================================================
# DateFilterHook tests
# ===========================================================================


def _ctx_with_dates(
    dates: list[ExtractedDate],
    *,
    place_dcids: tuple[str, ...] = (),
) -> HookContext:
    return HookContext(
        place_dcids=place_dcids,
        place_availability=None,
        retrieval_scores={},
        raw_candidates=(),
        dates=dates,
    )


# ---------------------------------------------------------------------------
# applies()
# ---------------------------------------------------------------------------


def test_date_filter_hook_applies_when_dates_present() -> None:
    hook = DateFilterHook()
    pred = _census_predicate()
    dates = [ExtractedDate(kind="range", start="2010", end=None)]
    ctx = _ctx_with_dates(dates)
    assert hook.applies(pred, (), ctx) is True


def test_date_filter_hook_does_not_apply_when_dates_empty() -> None:
    hook = DateFilterHook()
    pred = _census_predicate()
    ctx = _ctx_with_dates([])
    assert hook.applies(pred, (), ctx) is False


# ---------------------------------------------------------------------------
# _year + _overlaps + _union_range pure-helper unit tests
# ---------------------------------------------------------------------------


def test_year_helper_extracts_leading_4_digits() -> None:
    from dc_search.hooks import _year

    assert _year("2015") == 2015
    assert _year("2015-03") == 2015
    assert _year("2015-03-01") == 2015
    assert _year(None) is None
    assert _year("") is None
    assert _year("XXXX") is None


def test_overlaps_basic_inside() -> None:
    from dc_search.hooks import _overlaps

    # Coverage 2010-2024; point 2020 → keep
    assert _overlaps("2010", "2024", "2020", "2020") is True


def test_overlaps_basic_outside() -> None:
    from dc_search.hooks import _overlaps

    # Coverage 2010-2024; point 2008 → drop
    assert _overlaps("2010", "2024", "2008", "2008") is False


def test_overlaps_range_disjoint() -> None:
    from dc_search.hooks import _overlaps

    # Coverage ends 2012; window starts 2015 → drop
    assert _overlaps("2010", "2012", "2015", None) is False


def test_overlaps_range_overlap() -> None:
    from dc_search.hooks import _overlaps

    # Coverage 2010-2024; window from 2015 → keep
    assert _overlaps("2010", "2024", "2015", None) is True


def test_overlaps_no_evidence_returns_true() -> None:
    from dc_search.hooks import _overlaps

    # Both bounds None → no evidence → fail-open keep
    assert _overlaps(None, None, "2015", "2020") is True


def test_overlaps_open_bounds() -> None:
    from dc_search.hooks import _overlaps

    # start-only window: cov 2010-2012, window [2015,+∞) → drop
    assert _overlaps("2010", "2012", "2015", None) is False
    # end-only window: cov 2010-2012, window [-∞,2020] → keep
    assert _overlaps("2010", "2012", None, "2020") is True


def test_overlaps_mixed_granularity() -> None:
    from dc_search.hooks import _overlaps

    # Coverage "2015-03" to "2018-09-01"; point "2016" → keep
    assert _overlaps("2015-03", "2018-09-01", "2016", "2016") is True
    # Point "2019" → drop (cov_max = 2018)
    assert _overlaps("2015-03", "2018-09-01", "2019", "2019") is False


def test_union_range_none_paths() -> None:
    from dc_search.hooks import _union_range

    # Both-None first arg → returns b unchanged
    assert _union_range(None, ("2010", "2015")) == ("2010", "2015")
    # Widen open bounds
    assert _union_range(("2010", None), (None, "2020")) == ("2010", "2020")
    # Both-None bounds stay None
    assert _union_range((None, None), (None, None)) == (None, None)


# ---------------------------------------------------------------------------
# run() — latest no-op
# ---------------------------------------------------------------------------


def test_date_filter_hook_latest_no_op() -> None:
    """kind='latest' → hook is bypassed even if applies() fired."""
    hook = DateFilterHook()
    pred = _census_predicate()
    sv_set = ["SV_A", "SV_B"]
    result = _answer(sv_set)
    ctx = _ctx_with_dates([ExtractedDate(kind="latest", start=None, end=None)])

    from unittest.mock import patch

    with patch("dc_search.hooks.variable_date_coverage") as mock_cov:
        out = hook.run(pred, result, ctx)
        assert isinstance(out, AnswerCollection)

    mock_cov.assert_not_called()
    assert out is result


# ---------------------------------------------------------------------------
# run() — point inside/outside (custom var, placeless, via envelope)
# ---------------------------------------------------------------------------


def _empty_cov(*_args, **_kwargs):
    from dc_search.retrieval import DateCoverage

    return DateCoverage({}, {})


def _cov_with_envelope(v: str, earliest: str, latest: str):
    from dc_search.retrieval import DateCoverage

    return DateCoverage(
        envelopes={v: (earliest, latest)},
        entity_ranges={},
    )


def test_date_filter_hook_point_inside_kept() -> None:
    hook = DateFilterHook()
    pred = _census_predicate()
    result = _answer(["SV_A"])
    ctx = _ctx_with_dates([ExtractedDate(kind="point", start="2020", end=None)])

    with patch(
        "dc_search.hooks.variable_date_coverage",
        return_value=_cov_with_envelope("SV_A", "2010", "2024"),
    ):
        out = hook.run(pred, result, ctx)
        assert isinstance(out, AnswerCollection)

    assert isinstance(out, AnswerCollection)
    assert "SV_A" in out.sv_set


def test_date_filter_hook_point_outside_dropped() -> None:
    hook = DateFilterHook()
    pred = _census_predicate()
    result = _answer(["SV_A", "SV_B"])
    # SV_A covers 2010-2012; point 2015 → drop SV_A; SV_B is base-DC (map-absent → keep)
    ctx = _ctx_with_dates([ExtractedDate(kind="point", start="2015", end=None)])

    from dc_search.retrieval import DateCoverage

    cov = DateCoverage(envelopes={"SV_A": ("2010", "2012")}, entity_ranges={})

    with (
        patch("dc_search.hooks.variable_date_coverage", return_value=cov),
        patch("dc_search.hooks.variable_info_date_ranges", return_value={}),
    ):
        out = hook.run(pred, result, ctx)
        assert isinstance(out, AnswerCollection)

    assert "SV_A" not in out.sv_set
    assert "SV_B" in out.sv_set
    assert "date_filtered" in out.caveats
    assert out.date_filter is not None


# ---------------------------------------------------------------------------
# run() — range overlap / disjoint
# ---------------------------------------------------------------------------


def test_date_filter_hook_range_overlap_kept() -> None:
    hook = DateFilterHook()
    pred = _census_predicate()
    result = _answer(["SV_A"])
    ctx = _ctx_with_dates([ExtractedDate(kind="range", start="2015", end=None)])

    with patch(
        "dc_search.hooks.variable_date_coverage",
        return_value=_cov_with_envelope("SV_A", "2010", "2024"),
    ):
        out = hook.run(pred, result, ctx)
        assert isinstance(out, AnswerCollection)

    assert "SV_A" in out.sv_set


def test_date_filter_hook_range_disjoint_dropped() -> None:
    hook = DateFilterHook()
    pred = _census_predicate()
    result = _answer(["SV_A"])
    ctx = _ctx_with_dates([ExtractedDate(kind="range", start="2015", end=None)])

    with (
        patch(
            "dc_search.hooks.variable_date_coverage",
            return_value=_cov_with_envelope("SV_A", "2010", "2012"),
        ),
    ):
        out = hook.run(pred, result, ctx)
        assert isinstance(out, AnswerCollection)

    assert "SV_A" not in out.sv_set
    assert "date_filtered" in out.caveats


# ---------------------------------------------------------------------------
# run() — placeless uses envelope; placed uses per-entity union
# ---------------------------------------------------------------------------


def test_date_filter_hook_placed_uses_entity_ranges() -> None:
    from dc_search.retrieval import DateCoverage

    hook = DateFilterHook()
    pred = _census_predicate()
    result = _answer(["SV_A"])
    ctx = _ctx_with_dates(
        [ExtractedDate(kind="point", start="2020", end=None)],
        place_dcids=("country/KEN",),
    )
    # Envelope says 2010-2012 (would drop), but entity range says 2015-2024 (keep).
    cov = DateCoverage(
        envelopes={"SV_A": ("2010", "2012")},
        entity_ranges={("SV_A", "country/KEN"): ("2015", "2024")},
    )
    with patch("dc_search.hooks.variable_date_coverage", return_value=cov):
        out = hook.run(pred, result, ctx)
        assert isinstance(out, AnswerCollection)
    assert "SV_A" in out.sv_set


# ---------------------------------------------------------------------------
# run() — routing: base-DC placeless vs base-DC placed vs custom
# ---------------------------------------------------------------------------


def test_date_filter_hook_base_dc_placeless_uses_variable_info() -> None:
    """Base-DC var (map-absent, no places) → variable_info_date_ranges called."""
    hook = DateFilterHook()
    pred = _census_predicate()
    result = _answer(["BASE_SV"])
    ctx = _ctx_with_dates([ExtractedDate(kind="point", start="2020", end=None)])

    from dc_search.retrieval import DateCoverage

    empty_cov = DateCoverage({}, {})

    with (
        patch("dc_search.hooks.variable_date_coverage", return_value=empty_cov),
        patch(
            "dc_search.hooks.variable_info_date_ranges",
            return_value={"BASE_SV": ("2010", "2024")},
        ) as mock_info,
        patch("dc_search.hooks.observation_date_ranges") as mock_obs,
    ):
        out = hook.run(pred, result, ctx)
        assert isinstance(out, AnswerCollection)

    mock_info.assert_called_once()
    mock_obs.assert_not_called()
    assert "BASE_SV" in out.sv_set  # 2020 inside 2010-2024


def test_date_filter_hook_base_dc_placed_uses_observation() -> None:
    """Base-DC var (map-absent, with places) → observation_date_ranges called."""
    hook = DateFilterHook()
    pred = _census_predicate()
    result = _answer(["BASE_SV"])
    ctx = _ctx_with_dates(
        [ExtractedDate(kind="point", start="2020", end=None)],
        place_dcids=("country/KEN",),
    )

    from dc_search.retrieval import DateCoverage

    empty_cov = DateCoverage({}, {})

    with (
        patch("dc_search.hooks.variable_date_coverage", return_value=empty_cov),
        patch("dc_search.hooks.variable_info_date_ranges") as mock_info,
        patch(
            "dc_search.hooks.observation_date_ranges",
            return_value={("BASE_SV", "country/KEN"): ("2015", "2024")},
        ) as mock_obs,
    ):
        out = hook.run(pred, result, ctx)
        assert isinstance(out, AnswerCollection)

    mock_info.assert_not_called()
    mock_obs.assert_called_once()
    assert "BASE_SV" in out.sv_set


# ---------------------------------------------------------------------------
# run() — fail-open on helper exception
# ---------------------------------------------------------------------------


def test_date_filter_hook_fail_open_on_coverage_exception() -> None:
    hook = DateFilterHook()
    pred = _census_predicate()
    sv_set = ["SV_A", "SV_B"]
    result = _answer(sv_set)
    ctx = _ctx_with_dates([ExtractedDate(kind="point", start="2020", end=None)])

    with patch("dc_search.hooks.variable_date_coverage", side_effect=RuntimeError("boom")):
        out = hook.run(pred, result, ctx)
        assert isinstance(out, AnswerCollection)

    assert out is result  # all kept, unchanged
    assert "date_filtered" not in out.caveats


def test_date_filter_hook_base_helper_returns_empty_keeps_var() -> None:
    """Base-DC var with no range evidence (helper returns {}) → fail-open keep."""
    hook = DateFilterHook()
    pred = _census_predicate()
    result = _answer(["BASE_SV"])
    ctx = _ctx_with_dates([ExtractedDate(kind="point", start="2020", end=None)])

    from dc_search.retrieval import DateCoverage

    empty_cov = DateCoverage({}, {})

    with (
        patch("dc_search.hooks.variable_date_coverage", return_value=empty_cov),
        patch("dc_search.hooks.variable_info_date_ranges", return_value={}),
    ):
        out = hook.run(pred, result, ctx)
        assert isinstance(out, AnswerCollection)

    assert "BASE_SV" in out.sv_set
    assert "date_filtered" not in out.caveats


# ---------------------------------------------------------------------------
# run() — custom var in {V} but no {E,V} at resolved places → drop
# ---------------------------------------------------------------------------


def test_date_filter_hook_custom_absent_at_place_dropped() -> None:
    """3-state _range_for: custom var in envelopes but no {E,V} → drop."""
    from dc_search.retrieval import DateCoverage

    hook = DateFilterHook()
    pred = _census_predicate()
    result = _answer(["SV_A"])
    ctx = _ctx_with_dates(
        [ExtractedDate(kind="point", start="2020", end=None)],
        place_dcids=("country/KEN",),
    )
    # SV_A is in envelopes but no entity_range at country/KEN.
    cov = DateCoverage(
        envelopes={"SV_A": ("2010", "2024")},
        entity_ranges={},  # no {E,V} at country/KEN
    )
    with patch("dc_search.hooks.variable_date_coverage", return_value=cov):
        out = hook.run(pred, result, ctx)
        assert isinstance(out, AnswerCollection)

    assert "SV_A" not in out.sv_set
    assert "date_filtered" in out.caveats


# ---------------------------------------------------------------------------
# run() — empty after filter → sv_set stays empty (EmptyResultHook fires next)
# ---------------------------------------------------------------------------


def test_date_filter_hook_empty_after_filter_returns_empty_result() -> None:
    """Everything dropped → run() returns AnswerCollection with empty sv_set.

    Must NOT return AskClarification from this hook — EmptyResultHook handles that.
    """
    hook = DateFilterHook()
    pred = _census_predicate()
    result = _answer(["SV_A"])
    ctx = _ctx_with_dates([ExtractedDate(kind="point", start="2005", end=None)])

    # SV_A covers only 2010-2024; point 2005 → drop
    with patch(
        "dc_search.hooks.variable_date_coverage",
        return_value=_cov_with_envelope("SV_A", "2010", "2024"),
    ):
        out = hook.run(pred, result, ctx)
        assert isinstance(out, AnswerCollection)

    assert isinstance(out, AnswerCollection)
    assert out.sv_set == []
    assert "date_filtered" in out.caveats
    assert out.date_filter is not None


# ---------------------------------------------------------------------------
# Fix 3 — degenerate date window silently no-ops
# ---------------------------------------------------------------------------


def test_date_filter_hook_point_none_start_is_noop() -> None:
    """A point with start=None is a degenerate window — sv_set and caveats unchanged."""
    hook = DateFilterHook()
    pred = _census_predicate()
    sv_set = ["SV_A", "SV_B"]
    result = _answer(sv_set)
    ctx = _ctx_with_dates([ExtractedDate(kind="point", start=None, end=None)])

    with patch("dc_search.hooks.variable_date_coverage") as mock_cov:
        out = hook.run(pred, result, ctx)
        assert isinstance(out, AnswerCollection)

    mock_cov.assert_not_called()
    assert out is result
    assert "date_filtered" not in out.caveats
    assert out.sv_set == sv_set


def test_date_filter_hook_range_both_none_is_noop() -> None:
    """A range with both start=None and end=None is a degenerate window — no-op."""
    hook = DateFilterHook()
    pred = _census_predicate()
    sv_set = ["SV_A", "SV_B"]
    result = _answer(sv_set)
    ctx = _ctx_with_dates([ExtractedDate(kind="range", start=None, end=None)])

    with patch("dc_search.hooks.variable_date_coverage") as mock_cov:
        out = hook.run(pred, result, ctx)
        assert isinstance(out, AnswerCollection)

    mock_cov.assert_not_called()
    assert out is result
    assert "date_filtered" not in out.caveats


def test_date_filter_hook_since_2015_still_filters() -> None:
    """Open-ended 'since 2015' (start set, end None) still filters correctly."""
    hook = DateFilterHook()
    pred = _census_predicate()
    # SV_A covers 2010-2012 → disjoint with [2015,+∞) → dropped.
    result = _answer(["SV_A"])
    ctx = _ctx_with_dates([ExtractedDate(kind="range", start="2015", end=None)])

    with patch(
        "dc_search.hooks.variable_date_coverage",
        return_value=_cov_with_envelope("SV_A", "2010", "2012"),
    ):
        out = hook.run(pred, result, ctx)
        assert isinstance(out, AnswerCollection)

    assert "SV_A" not in out.sv_set
    assert "date_filtered" in out.caveats


def test_date_filter_hook_before_2010_still_filters() -> None:
    """Open-ended 'before 2010' (start None, end set) still filters correctly."""
    hook = DateFilterHook()
    pred = _census_predicate()
    # SV_A covers 2015-2024 → disjoint with [-∞,2010] → dropped.
    result = _answer(["SV_A"])
    ctx = _ctx_with_dates([ExtractedDate(kind="range", start=None, end="2010")])

    with patch(
        "dc_search.hooks.variable_date_coverage",
        return_value=_cov_with_envelope("SV_A", "2015", "2024"),
    ):
        out = hook.run(pred, result, ctx)
        assert isinstance(out, AnswerCollection)

    assert "SV_A" not in out.sv_set
    assert "date_filtered" in out.caveats


# ---------------------------------------------------------------------------
# run() — caveat + date_filter populated when something dropped
# ---------------------------------------------------------------------------


def test_date_filter_hook_sets_date_filter_field() -> None:
    from dc_search.extraction import ExtractedDate as ED

    hook = DateFilterHook()
    pred = _census_predicate()
    result = _answer(["SV_A", "SV_B"])
    window = ED(kind="range", start="2015", end="2020")
    ctx = _ctx_with_dates([window])

    from dc_search.retrieval import DateCoverage

    # SV_A in 2010-2012 → drop; SV_B map-absent → keep (no evidence)
    cov = DateCoverage(envelopes={"SV_A": ("2010", "2012")}, entity_ranges={})

    with (
        patch("dc_search.hooks.variable_date_coverage", return_value=cov),
        patch("dc_search.hooks.variable_info_date_ranges", return_value={}),
    ):
        out = hook.run(pred, result, ctx)
        assert isinstance(out, AnswerCollection)

    assert "date_filtered" in out.caveats
    assert out.date_filter is not None
    assert out.date_filter.start == "2015"
    assert out.date_filter.end == "2020"


# ===========================================================================
# _build_variables projector tests
# ===========================================================================


def _make_ctx_for_build_vars(
    candidates: list,
    *,
    place_dcids: tuple = (),
    place_availability: frozenset | None = None,
    retrieval_scores: dict | None = None,
    dcid_to_sentence: dict | None = None,
    dcid_to_date_range: dict | None = None,
) -> HookContext:
    return HookContext(
        place_dcids=place_dcids,
        place_availability=place_availability,
        retrieval_scores=retrieval_scores or {},
        raw_candidates=tuple(candidates),
        dcid_to_sentence=dcid_to_sentence or {},
        dcid_to_date_range=dcid_to_date_range or {},
    )


def _sv(dcid: str, **kwargs) -> StatVarFeatures:
    defaults = {
        "dcid": dcid,
        "name": f"Name of {dcid}",
        "population_type": ["Person"],
        "measured_property": ["count"],
        "stat_type": ["measuredValue"],
    }
    defaults.update(kwargs)
    return StatVarFeatures(**defaults)


class TestBuildVariables:
    def test_maps_feature_fields(self):
        """_build_variables populates name/description/unit/measured_property etc."""
        from dc_search.hooks import _build_variables

        sv = StatVarFeatures(
            dcid="LifeExpectancy_Person",
            name="Life Expectancy",
            description="Life expectancy at birth.",
            population_type=["Person"],
            measured_property=["lifeExpectancy"],
            stat_type=["measuredValue"],
            unit=["years"],
        )
        ctx = _make_ctx_for_build_vars([sv])
        result = _build_variables(["LifeExpectancy_Person"], ctx)

        assert len(result) == 1
        rv = result[0]
        assert rv.dcid == "LifeExpectancy_Person"
        assert rv.name == "Life Expectancy"
        assert rv.description == "Life expectancy at birth."
        assert rv.population_type == "Person"
        assert rv.measured_property == "lifeExpectancy"
        assert rv.stat_type == "measuredValue"
        assert rv.unit == "years"

    def test_available_at_place_none_when_no_place_dcids(self):
        """available_at_place is None when place_dcids is empty."""
        from dc_search.hooks import _build_variables

        sv = _sv("SV_A")
        ctx = _make_ctx_for_build_vars(
            [sv],
            place_dcids=(),  # no place resolved
            place_availability=frozenset({"SV_A"}),  # availability computed but place empty
        )
        result = _build_variables(["SV_A"], ctx)
        assert result[0].available_at_place is None

    def test_available_at_place_none_when_place_availability_is_none(self):
        """B4 regression: available_at_place is None when place_availability is None
        even with non-empty place_dcids (places resolved but availability not computed)."""
        from dc_search.hooks import _build_variables

        sv = _sv("SV_A")
        ctx = _make_ctx_for_build_vars(
            [sv],
            place_dcids=("country/KEN",),  # place resolved
            place_availability=None,  # availability NOT computed
        )
        result = _build_variables(["SV_A"], ctx)
        assert result[0].available_at_place is None, (
            "B4: available_at_place must be None when place_availability is None, "
            "even with non-empty place_dcids"
        )

    def test_available_at_place_true_when_in_availability(self):
        """available_at_place=True when dcid is in place_availability."""
        from dc_search.hooks import _build_variables

        sv = _sv("SV_A")
        ctx = _make_ctx_for_build_vars(
            [sv],
            place_dcids=("country/KEN",),
            place_availability=frozenset({"SV_A", "SV_B"}),
        )
        result = _build_variables(["SV_A"], ctx)
        assert result[0].available_at_place is True

    def test_available_at_place_false_when_not_in_availability(self):
        """available_at_place=False when dcid is NOT in place_availability."""
        from dc_search.hooks import _build_variables

        sv = _sv("SV_A")
        ctx = _make_ctx_for_build_vars(
            [sv],
            place_dcids=("country/KEN",),
            place_availability=frozenset({"SV_OTHER"}),
        )
        result = _build_variables(["SV_A"], ctx)
        assert result[0].available_at_place is False

    def test_date_range_projected_from_dcid_to_date_range(self):
        """date_range is a DateRange object projected from ctx.dcid_to_date_range."""
        from dc_search.hooks import _build_variables
        from dc_search.predicate import DateRange

        sv = _sv("SV_A")
        ctx = _make_ctx_for_build_vars(
            [sv],
            dcid_to_date_range={"SV_A": ("2010", "2024")},
        )
        result = _build_variables(["SV_A"], ctx)
        dr = result[0].date_range
        assert dr is not None
        assert isinstance(dr, DateRange)
        assert dr.earliest == "2010"
        assert dr.latest == "2024"

    def test_date_range_none_when_not_in_map(self):
        """date_range is None when dcid is absent from dcid_to_date_range."""
        from dc_search.hooks import _build_variables

        sv = _sv("SV_A")
        ctx = _make_ctx_for_build_vars([sv], dcid_to_date_range={})
        result = _build_variables(["SV_A"], ctx)
        assert result[0].date_range is None

    def test_missing_feature_yields_dcid_only(self):
        """A DCID not in raw_candidates yields a DCID-only ResolvedVariable with None fields."""
        from dc_search.hooks import _build_variables

        # raw_candidates is empty — DCID not in feature map
        ctx = _make_ctx_for_build_vars([])
        result = _build_variables(["UNKNOWN_SV"], ctx)

        assert len(result) == 1
        rv = result[0]
        assert rv.dcid == "UNKNOWN_SV"
        assert rv.name is None
        assert rv.description is None
        assert rv.population_type is None
        assert rv.available_at_place is None
        assert rv.date_range is None

    def test_score_from_retrieval_scores(self):
        """score is populated from ctx.retrieval_scores."""
        from dc_search.hooks import _build_variables

        sv = _sv("SV_A")
        ctx = _make_ctx_for_build_vars([sv], retrieval_scores={"SV_A": 0.9})
        result = _build_variables(["SV_A"], ctx)
        assert result[0].score == pytest.approx(0.9)

    def test_matched_sentence_from_dcid_to_sentence(self):
        """matched_sentence is populated from ctx.dcid_to_sentence."""
        from dc_search.hooks import _build_variables

        sv = _sv("SV_A")
        ctx = _make_ctx_for_build_vars([sv], dcid_to_sentence={"SV_A": "life expectancy"})
        result = _build_variables(["SV_A"], ctx)
        assert result[0].matched_sentence == "life expectancy"

    def test_multiple_svs_preserves_order(self):
        """Output order matches sv_set order."""
        from dc_search.hooks import _build_variables

        sv_a = _sv("SV_A")
        sv_b = _sv("SV_B")
        ctx = _make_ctx_for_build_vars([sv_a, sv_b])
        result = _build_variables(["SV_B", "SV_A"], ctx)
        assert result[0].dcid == "SV_B"
        assert result[1].dcid == "SV_A"
