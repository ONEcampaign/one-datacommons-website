"""Tests for the hook pipeline (hooks.py)."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from dc_search import retrieval
from dc_search.extraction import ExtractedDate
from dc_search.hooks import (
    CrsDacRecipientSetHook,
    CrsDacWildcardExpansionHook,
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
from dc_search.hooks.crs_dac_recipient_set import HOOK_NAME as _CRS_RECIPIENT_SET_HOOK_NAME
from dc_search.predicate import (
    AnswerCollection,
    AskClarification,
    Caveat,
    Confidence,
    Predicate,
    ResolvedVariable,
)
from dc_search.retrieval import StatVarFeatures, VariableGroupInfo

# ---------------------------------------------------------------------------
# Minimal test candidates
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

# Candidate with memberOf: represents a real group different from the synthesized DCID,
# exercises drift/recovery. Leading dc/g is an unrelated rollup and must be ignored.
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
# CrsDac SVG hooks: synthesis, retrieval recovery, and wildcard expansion
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
# DenominatorImplicitHook: implicit denominator caveat
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
# filtering_degraded caveat: fail-open signal
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
# Data-driven confidence via RetrievalQualityHook
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
# Hook short-circuit on AskClarification
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
# Unknown namespace with candidates
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


def test_materialize_many_preserves_enriched_variables_from_subresults() -> None:
    """N-tuple union preserves each sub-result's enriched variables.

    For cross-product × projection queries ("malaria and HIV grants to African
    countries"), each sub-predicate's ProjectionEnrichmentHook builds variables
    with backup-fetched names and donor-narrowed availability. The union must
    not rebuild from ``ctx.raw_candidates`` / pre-narrow ``ctx.place_availability``
    — both would silently overwrite that enrichment.
    """
    predicates = (_ken_predicate(), _tgo_predicate())
    # ctx has empty place set + None availability, so _build_variables(ctx)
    # alone would emit available_at_place=None and names taken straight from
    # raw_candidates ("COVID grants to Kenya"/"...Togo"). Distinct markers
    # below let us catch a clobber.
    ctx = HookContext(
        place_dcids=(),
        place_availability=None,
        retrieval_scores={_KEN_SV: 0.9, _TGO_SV: 0.7},
        raw_candidates=tuple(_MULTI_CANDIDATES),
    )

    enriched_ken = ResolvedVariable(
        dcid=_KEN_SV, name="ENRICHED_KEN", available_at_place=True
    )
    enriched_tgo = ResolvedVariable(
        dcid=_TGO_SV, name="ENRICHED_TGO", available_at_place=True
    )

    def _fake_materialize(
        pred: Predicate,
        candidates: list[StatVarFeatures],
        *,
        ctx: HookContext,
    ) -> AnswerCollection:
        recipient = pred.constraints.get("DevelopmentFinanceRecipient")
        var = enriched_ken if recipient == "country/KEN" else enriched_tgo
        return AnswerCollection(
            predicate=pred,
            sv_set=[var.dcid],
            svg_dcids=(),
            collection_dcid=None,
            confidence="high",
            caveats=[],
            variables=[var],
        )

    with patch(
        "dc_search.hooks.materialization.materialize_via_hooks",
        side_effect=_fake_materialize,
    ):
        result = materialize_many(predicates, _MULTI_CANDIDATES, ctx=ctx)

    assert isinstance(result, AnswerCollection)
    by_dcid = {v.dcid: v for v in result.variables}
    assert by_dcid[_KEN_SV].name == "ENRICHED_KEN"
    assert by_dcid[_KEN_SV].available_at_place is True
    assert by_dcid[_TGO_SV].name == "ENRICHED_TGO"
    assert by_dcid[_TGO_SV].available_at_place is True
    # Variable order tracks sv_set order, not accumulation order.
    assert [v.dcid for v in result.variables] == result.sv_set


def test_materialize_many_falls_back_for_unenriched_subresults() -> None:
    """N-tuple union falls back to ctx-build for sub-results with empty variables.

    A sub-predicate whose hook chain didn't trigger projection enrichment
    returns an AnswerCollection with empty ``variables`` (the 1-tuple branch
    handles that via its ``if not result.variables`` guard; the N-tuple path
    needs the equivalent in the union step). This test mixes one enriched
    sub-result with one bare one and confirms both DCIDs are represented.
    """
    predicates = (_ken_predicate(), _tgo_predicate())
    ctx = HookContext(
        place_dcids=(),
        place_availability=None,
        retrieval_scores={_KEN_SV: 0.9, _TGO_SV: 0.7},
        raw_candidates=tuple(_MULTI_CANDIDATES),
    )

    enriched_ken = ResolvedVariable(
        dcid=_KEN_SV, name="ENRICHED_KEN", available_at_place=True
    )

    def _fake_materialize(
        pred: Predicate,
        candidates: list[StatVarFeatures],
        *,
        ctx: HookContext,
    ) -> AnswerCollection:
        recipient = pred.constraints.get("DevelopmentFinanceRecipient")
        if recipient == "country/KEN":
            return AnswerCollection(
                predicate=pred,
                sv_set=[_KEN_SV],
                svg_dcids=(),
                collection_dcid=None,
                confidence="high",
                caveats=[],
                variables=[enriched_ken],
            )
        return AnswerCollection(
            predicate=pred,
            sv_set=[_TGO_SV],
            svg_dcids=(),
            collection_dcid=None,
            confidence="medium",
            caveats=[],
            variables=[],
        )

    with patch(
        "dc_search.hooks.materialization.materialize_via_hooks",
        side_effect=_fake_materialize,
    ):
        result = materialize_many(predicates, _MULTI_CANDIDATES, ctx=ctx)

    assert isinstance(result, AnswerCollection)
    by_dcid = {v.dcid: v for v in result.variables}
    # Enriched sub-result survives untouched.
    assert by_dcid[_KEN_SV].name == "ENRICHED_KEN"
    assert by_dcid[_KEN_SV].available_at_place is True
    # Bare sub-result gets the ctx-build fallback (name from raw_candidates,
    # tri-state availability None because place_dcids is empty).
    assert by_dcid[_TGO_SV].name == "COVID grants to Togo"
    assert by_dcid[_TGO_SV].available_at_place is None


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

    # bad_pred's synthesized SVG resolves to an empty group; CrsDacRetrievalRecoveryHook
    # falls through to under_specified. ken_pred's SVG resolves to a populated VG so
    # the AnswerCollection from that sub-predicate stays valid up to the point where
    # bad_pred poisons the union.
    def _vg_side_effect(*, dcid: str) -> VariableGroupInfo:
        if "KEN" in dcid:
            return _KEN_VG
        return _UNVERIFIED_VG

    with patch("dc_search.hooks.variable_group", side_effect=_vg_side_effect):
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
# PlaceAvailabilityHook applies — guard removal
# ---------------------------------------------------------------------------


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


def test_place_availability_hook_applies_when_place_is_constraint_value() -> None:
    """PlaceAvailabilityHook.applies returns True when places match constraint values.

    ctx.place_dcids is the donor (entity) set; it never contains constraint-bound
    places, so no skip guard is needed.
    """
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
        place_dcids=("country/USA",),  # donor, not a constraint value
        place_availability=frozenset({"ONE/CRS_DAC/COVID19control-ODAGrants-KEN"}),
        retrieval_scores={},
        raw_candidates=(),
    )

    assert hook.applies(predicate, (), ctx) is True


# ===========================================================================
# Piece D — SVG recovery for empty sv_set
# ===========================================================================

_NGA_SV = "ONE/CRS_DAC/Malariacontrol-ODAGrants-NGA"

_NGA_VG_WITH_CHILD_VARS = VariableGroupInfo(
    dcid="ONE/g/DevelopmentFinance_DevelopmentFinancePurpose-DACMalariacontrol_DevelopmentFinanceRecipient-CountryNGA_DevelopmentFinanceScheme-ODAGrants",
    name="Malaria grants to Nigeria",
    parents=[],
    child_groups=[],
    child_vars=[{"dcid": _NGA_SV, "name": "Malaria control grants NGA"}],
)

_NGA_GROUP_1 = "ONE/g/DevelopmentFinance_NGA_group1"
_NGA_VG_WITH_CHILD_GROUPS = VariableGroupInfo(
    dcid="ONE/g/DevelopmentFinance_DevelopmentFinancePurpose-DACMalariacontrol_DevelopmentFinanceRecipient-CountryNGA_DevelopmentFinanceScheme-ODAGrants",
    name="Malaria grants to Nigeria",
    parents=[],
    child_groups=[{"dcid": _NGA_GROUP_1, "name": "NGA group 1"}],
    child_vars=[],
)

_NGA_PREDICATE = Predicate(
    population_type="DevelopmentFinance",
    measured_property="DevelopmentFinanceFlow",
    constraints={
        "DevelopmentFinancePurpose": "DAC/Malariacontrol",
        "DevelopmentFinanceRecipient": "country/NGA",
        "DevelopmentFinanceScheme": "ODAGrants",
    },
)

_NGA_FEATURES = StatVarFeatures(
    dcid=_NGA_SV,
    name="Health [Grants to Nigeria]",
    population_type=["DevelopmentFinance"],
    measured_property=["DevelopmentFinanceFlow"],
    stat_type=["measuredValue"],
)


def test_crs_dac_piece_d_recovers_via_child_vars() -> None:
    """Empty sv_set + variable_group with child_vars yields recovered AnswerCollection.

    Expected: recovered DCID in sv_set, features in variables, svg_dcids set, confidence high.
    """
    # Empty candidates — recipient NGA not in retrieved pool
    ctx = _make_ctx([])

    with (
        patch("dc_search.hooks.variable_group", return_value=_NGA_VG_WITH_CHILD_VARS),
        patch(
            "dc_search.hooks.stat_var_features_batch",
            return_value={_NGA_SV: _NGA_FEATURES},
        ) as mock_feat,
    ):
        result = materialize_via_hooks(_NGA_PREDICATE, [], ctx=ctx)

    assert isinstance(result, AnswerCollection), f"Expected AnswerCollection, got {result}"
    assert _NGA_SV in result.sv_set
    assert result.svg_dcids
    assert result.confidence == "high"
    mock_feat.assert_called_once()
    # variables carry names from the fetched features
    assert any(v.name is not None for v in result.variables), (
        "Piece D variables should have names from stat_var_features_batch"
    )
    name_found = any("Nigeria" in (v.name or "") for v in result.variables)
    assert name_found, (
        f"Expected 'Nigeria' in variable names, got {[v.name for v in result.variables]}"
    )


def test_crs_dac_piece_d_recovers_via_child_groups() -> None:
    """Piece D: empty sv_set + variable_group has child_groups → child_vars_of_groups called."""
    ctx = _make_ctx([])

    with (
        patch("dc_search.hooks.variable_group", return_value=_NGA_VG_WITH_CHILD_GROUPS),
        patch(
            "dc_search.hooks.registry.child_vars_of_groups",
            return_value={_NGA_GROUP_1: [_NGA_SV]},
        ),
        patch(
            "dc_search.hooks.stat_var_features_batch",
            return_value={_NGA_SV: _NGA_FEATURES},
        ) as mock_feat,
    ):
        result = materialize_via_hooks(_NGA_PREDICATE, [], ctx=ctx)

    assert isinstance(result, AnswerCollection), f"Expected AnswerCollection, got {result}"
    assert _NGA_SV in result.sv_set
    mock_feat.assert_called_once()


def test_crs_dac_piece_d_fails_open_on_variable_group_exception() -> None:
    """Piece D: variable_group raises → fail-open → under_specified AskClarification."""
    ctx = _make_ctx([])

    with patch("dc_search.hooks.variable_group", side_effect=RuntimeError("network error")):
        result = materialize_via_hooks(_NGA_PREDICATE, [], ctx=ctx)

    assert isinstance(result, AskClarification)
    assert result.reason == "under_specified"


def test_crs_dac_piece_d_fails_open_on_empty_group() -> None:
    """Piece D: variable_group returns empty child_vars and child_groups → under_specified."""
    ctx = _make_ctx([])

    with patch("dc_search.hooks.variable_group", return_value=_UNVERIFIED_VG):
        result = materialize_via_hooks(_NGA_PREDICATE, [], ctx=ctx)

    assert isinstance(result, AskClarification)
    assert result.reason == "under_specified"


# ===========================================================================
# materialize_many — entity-set semantics (ctx.place_dcids used directly)
# ===========================================================================


def test_materialize_many_single_predicate_uses_ctx_place_dcids() -> None:
    """Single-predicate path uses ctx.place_dcids as donor entity set.

    When place_dcids=("country/USA",), available_at_place reflects USA as the donor.
    """
    predicate = _ken_predicate()
    avail = frozenset({_KEN_SV})
    ctx = HookContext(
        place_dcids=("country/USA",),
        place_availability=avail,
        retrieval_scores={},
        raw_candidates=tuple(_MULTI_CANDIDATES),
    )

    with patch("dc_search.hooks.variable_group", return_value=_KEN_VG):
        result = materialize_many((predicate,), _MULTI_CANDIDATES, ctx=ctx)

    assert isinstance(result, AnswerCollection)
    assert _KEN_SV in result.sv_set
    ken_var = next((v for v in result.variables if v.dcid == _KEN_SV), None)
    assert ken_var is not None
    assert ken_var.available_at_place is True


def test_materialize_many_single_predicate_empty_place_dcids_yields_none_avail() -> None:
    """Single-predicate path with place_dcids=() → available_at_place is None.

    When no donor remains (all places are recipients), availability is omitted.
    """
    predicate = _ken_predicate()
    ctx = HookContext(
        place_dcids=(),
        place_availability=frozenset({_KEN_SV}),
        retrieval_scores={},
        raw_candidates=tuple(_MULTI_CANDIDATES),
    )

    with patch("dc_search.hooks.variable_group", return_value=_KEN_VG):
        result = materialize_many((predicate,), _MULTI_CANDIDATES, ctx=ctx)

    assert isinstance(result, AnswerCollection)
    ken_var = next((v for v in result.variables if v.dcid == _KEN_SV), None)
    assert ken_var is not None
    assert ken_var.available_at_place is None


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
    """4 CRS_DAC sub-predicates trigger 2 client.node.fetch calls (batch pre-warm).

    Without pre-warm there would be 8 calls (2 per sub-predicate × 4).
    """
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
    """1-tuple input skips variable_groups_batch pre-warm."""
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
    # SV_A: 2010–2012 (point 2015 is outside); SV_B is base-DC (no coverage → kept)
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


# ===========================================================================
# CrsDacRecipientSetHook tests
# ===========================================================================

# Probe-confirmed DCIDs: purpose=DAC/Malariacontrol, scheme=ODAGrants,
# per-country SV suffix = ISO3.
_AFRICA_SV = "ONE/CRS_DAC/Malariacontrol-ODAGrants-F"  # aggregate, suffix F (not ISO3)
_KEN_MALARIA_SV = "ONE/CRS_DAC/Malariacontrol-ODAGrants-KEN"
_TGO_MALARIA_SV = "ONE/CRS_DAC/Malariacontrol-ODAGrants-TGO"
_ZAF_MALARIA_SV = "ONE/CRS_DAC/Malariacontrol-ODAGrants-ZAF"
_OTHER_SV = "ONE/CRS_DAC/OtherPurpose-ODAGrants-KEN"  # different purpose — not in family


def _set_pred(
    *,
    children: frozenset[str] | None = None,
    purpose: str | None = "DAC/Malariacontrol",
    scheme: str | None = "ODAGrants",
    sv_set: list[str] | None = None,
) -> tuple[Predicate, AnswerCollection]:
    """Build a set-valued DevelopmentFinance predicate + seed result."""
    pred = Predicate(
        population_type="DevelopmentFinance",
        measured_property="DevelopmentFinanceFlow",
        constraints={
            "DevelopmentFinancePurpose": purpose,
            "DevelopmentFinanceRecipient": "DAC/Africa",
            "DevelopmentFinanceScheme": scheme,
        },
        constraint_sets={
            "DevelopmentFinanceRecipient": children
            if children is not None
            else frozenset({"country/KEN", "country/TGO"})
        },
    )
    result = AnswerCollection(
        predicate=pred,
        sv_set=sv_set if sv_set is not None else [_AFRICA_SV],
        confidence="medium",
        caveats=[],
    )
    return pred, result


def _set_ctx(candidates: list[StatVarFeatures] | None = None) -> HookContext:
    return HookContext(
        place_dcids=(),
        place_availability=None,
        retrieval_scores={},
        raw_candidates=tuple(candidates or []),
    )


# Mock inverse-arc response: purpose-set has KEN/TGO/ZAF, scheme-set has all 4.
_MOCK_ARCS_BASE = {
    "DAC/Malariacontrol": frozenset({_KEN_MALARIA_SV, _TGO_MALARIA_SV, _ZAF_MALARIA_SV}),
    "ODAGrants": frozenset({_KEN_MALARIA_SV, _TGO_MALARIA_SV, _ZAF_MALARIA_SV, _OTHER_SV}),
}


class TestCrsDacRecipientSetHook:
    """Unit tests for CrsDacRecipientSetHook (inverse-arc materialization)."""

    def test_applies_true_for_devfinance_with_recipient_constraint_set(self):
        hook = CrsDacRecipientSetHook()
        pred, _ = _set_pred()
        assert hook.applies(pred, (), _set_ctx()) is True

    def test_applies_false_no_constraint_sets(self):
        """Scalar DevelopmentFinance predicate → applies() False (zero added fetches)."""
        hook = CrsDacRecipientSetHook()
        pred = Predicate(
            population_type="DevelopmentFinance",
            measured_property="DevelopmentFinanceFlow",
            constraints={"DevelopmentFinanceRecipient": "country/KEN"},
        )
        assert hook.applies(pred, (), _set_ctx()) is False

    def test_applies_false_non_devfinance(self):
        """Non-DevelopmentFinance predicate → applies() False."""
        hook = CrsDacRecipientSetHook()
        pred = Predicate(
            population_type="Person",
            measured_property="count",
            constraints={},
            constraint_sets={"DevelopmentFinanceRecipient": frozenset({"country/KEN"})},
        )
        assert hook.applies(pred, (), _set_ctx()) is False

    def test_applies_false_empty_constraint_sets(self):
        """Empty constraint_sets → applies() False."""
        hook = CrsDacRecipientSetHook()
        pred = Predicate(
            population_type="DevelopmentFinance",
            measured_property="DevelopmentFinanceFlow",
            constraints={},
            constraint_sets={},
        )
        assert hook.applies(pred, (), _set_ctx()) is False

    def test_b2_patch_targets_resolve(self):
        """Smoke: both B2 patch targets must exist as importable attributes."""
        import dc_search.hooks as _h
        import dc_search.retrieval as _r

        assert callable(getattr(_r, "svs_by_inverse_arcs", None)), (
            "dc_search.retrieval.svs_by_inverse_arcs must exist"
        )
        assert callable(getattr(_h, "stat_var_features_batch", None)), (
            "dc_search.hooks.stat_var_features_batch must exist"
        )

    def test_family_intersect_per_country_union_with_aggregate(self):
        """Family intersect → per-country union; aggregate from result.sv_set stays first."""
        hook = CrsDacRecipientSetHook()
        pred, result = _set_pred()  # children = KEN, TGO; seed sv_set = [AFRICA_SV]
        ctx = _set_ctx()

        with (
            patch(
                "dc_search.retrieval.svs_by_inverse_arcs",
                return_value=_MOCK_ARCS_BASE,
            ) as mock_arcs,
            patch(
                "dc_search.hooks.stat_var_features_batch",
                return_value={},
            ),
        ):
            out = hook.run(pred, result, ctx)

        assert isinstance(out, AnswerCollection)
        # Aggregate (AFRICA_SV, suffix F) stays first from the seeded result.sv_set.
        assert out.sv_set[0] == _AFRICA_SV
        # KEN and TGO are included (they are in the family AND in child_iso3).
        assert _KEN_MALARIA_SV in out.sv_set
        assert _TGO_MALARIA_SV in out.sv_set
        # ZAF is in the family but NOT in child_iso3 (children = KEN, TGO only).
        assert _ZAF_MALARIA_SV not in out.sv_set
        # set_valued_recipient: semantic "contained-in expansion" signal always present.
        assert "set_valued_recipient" in out.caveats
        # set_valued_answer: size signal — absent here (3 SVs, no cap truncation).
        assert "set_valued_answer" not in out.caveats
        # Exactly ONE svs_by_inverse_arcs call.
        assert mock_arcs.call_count == 1

    def test_i1_aggregate_survives_cap(self, monkeypatch):
        """I1: aggregate (in result.sv_set) survives the SV cap."""
        import dc_search.hooks.crs_dac_recipient_set as _sr

        monkeypatch.setattr(_sr, "_CRS_DAC_SV_CAP", 2)

        hook = CrsDacRecipientSetHook()
        pred, result = _set_pred()
        ctx = _set_ctx()

        with (
            patch(
                "dc_search.retrieval.svs_by_inverse_arcs",
                return_value=_MOCK_ARCS_BASE,
            ),
            patch("dc_search.hooks.stat_var_features_batch", return_value={}),
        ):
            out = hook.run(pred, result, ctx)

        assert isinstance(out, AnswerCollection)
        assert len(out.sv_set) <= 2
        # Aggregate must be present despite the cap.
        assert _AFRICA_SV in out.sv_set

    def test_i4_order_independence(self):
        """I4: different frozenset literals for the same children produce identical results."""
        hook = CrsDacRecipientSetHook()
        # Two predicates with the same child DCIDs but different frozenset construction.
        pred_a = Predicate(
            population_type="DevelopmentFinance",
            measured_property="DevelopmentFinanceFlow",
            constraints={
                "DevelopmentFinancePurpose": "DAC/Malariacontrol",
                "DevelopmentFinanceRecipient": "DAC/Africa",
                "DevelopmentFinanceScheme": "ODAGrants",
            },
            constraint_sets={
                "DevelopmentFinanceRecipient": frozenset({"country/KEN", "country/TGO"})
            },
        )
        pred_b = Predicate(
            population_type="DevelopmentFinance",
            measured_property="DevelopmentFinanceFlow",
            constraints={
                "DevelopmentFinancePurpose": "DAC/Malariacontrol",
                "DevelopmentFinanceRecipient": "DAC/Africa",
                "DevelopmentFinanceScheme": "ODAGrants",
            },
            constraint_sets={
                "DevelopmentFinanceRecipient": frozenset({"country/TGO", "country/KEN"})
            },
        )
        result_a = AnswerCollection(
            predicate=pred_a, sv_set=[_AFRICA_SV], confidence="medium", caveats=[]
        )
        result_b = AnswerCollection(
            predicate=pred_b, sv_set=[_AFRICA_SV], confidence="medium", caveats=[]
        )
        ctx = _set_ctx()

        call_args: list = []

        def _fake_arcs(**kwargs):
            call_args.append(kwargs)
            return _MOCK_ARCS_BASE

        with (
            patch("dc_search.retrieval.svs_by_inverse_arcs", side_effect=_fake_arcs),
            patch("dc_search.hooks.stat_var_features_batch", return_value={}),
        ):
            out_a = hook.run(pred_a, result_a, ctx)
            out_b = hook.run(pred_b, result_b, ctx)

        assert isinstance(out_a, AnswerCollection)
        assert isinstance(out_b, AnswerCollection)
        # Both runs produce identical sv_sets (sorted per_country).
        assert out_a.sv_set == out_b.sv_set
        # The value_dcids tuple passed to svs_by_inverse_arcs is identical both times.
        assert call_args[0]["value_dcids"] == call_args[1]["value_dcids"]

    def test_suffix_filter_excludes_f_aggregate(self):
        """The -F aggregate (suffix 'F', not an ISO3) is NOT added by the hook.

        It should only appear in out.sv_set because it was seeded in result.sv_set
        (scalar aggregate path), never via the suffix filter.
        """
        hook = CrsDacRecipientSetHook()
        # Add the -F aggregate to the family mock.
        arcs_with_f = {
            "DAC/Malariacontrol": frozenset({
                _AFRICA_SV, _KEN_MALARIA_SV, _TGO_MALARIA_SV
            }),
            "ODAGrants": frozenset({
                _AFRICA_SV, _KEN_MALARIA_SV, _TGO_MALARIA_SV
            }),
        }
        pred, result = _set_pred()  # seed sv_set already has AFRICA_SV
        ctx = _set_ctx()

        def _fake_arcs(**kwargs):
            return arcs_with_f

        with (
            patch("dc_search.retrieval.svs_by_inverse_arcs", side_effect=_fake_arcs),
            patch("dc_search.hooks.stat_var_features_batch", return_value={}),
        ):
            out = hook.run(pred, result, ctx)

        assert isinstance(out, AnswerCollection)
        # AFRICA_SV is in sv_set (seeded via result.sv_set scalar path).
        assert _AFRICA_SV in out.sv_set
        # KEN and TGO are added via suffix filter.
        assert _KEN_MALARIA_SV in out.sv_set
        assert _TGO_MALARIA_SV in out.sv_set
        # AFRICA_SV position: it must be at index 0 (from seeded result.sv_set, not suffix filter).
        assert out.sv_set[0] == _AFRICA_SV

    def test_wildcard_guard_purpose_none(self):
        """Wildcard purpose=None → fail-open, svs_by_inverse_arcs NOT called."""
        hook = CrsDacRecipientSetHook()
        pred, result = _set_pred(purpose=None)
        ctx = _set_ctx()

        with patch("dc_search.retrieval.svs_by_inverse_arcs") as mock_arcs:
            out = hook.run(pred, result, ctx)

        mock_arcs.assert_not_called()
        assert out is result  # unchanged

    def test_wildcard_guard_scheme_none(self):
        """Wildcard scheme=None → fail-open, svs_by_inverse_arcs NOT called."""
        hook = CrsDacRecipientSetHook()
        pred, result = _set_pred(scheme=None)
        ctx = _set_ctx()

        with patch("dc_search.retrieval.svs_by_inverse_arcs") as mock_arcs:
            out = hook.run(pred, result, ctx)

        mock_arcs.assert_not_called()
        assert out is result  # unchanged

    def test_fail_open_empty_family(self):
        """svs_by_inverse_arcs returns {} (empty family) → fail-open, result unchanged."""
        hook = CrsDacRecipientSetHook()
        pred, result = _set_pred()
        ctx = _set_ctx()

        with patch("dc_search.retrieval.svs_by_inverse_arcs", return_value={}):
            out = hook.run(pred, result, ctx)

        assert out is result

    def test_fail_open_empty_per_country(self):
        """Family non-empty but no member suffix matches child ISO3 → fail-open."""
        hook = CrsDacRecipientSetHook()
        pred, result = _set_pred(children=frozenset({"country/ZZZ"}))  # ZZZ not in family
        ctx = _set_ctx()

        with patch(
            "dc_search.retrieval.svs_by_inverse_arcs",
            return_value=_MOCK_ARCS_BASE,  # has KEN/TGO/ZAF but not ZZZ
        ):
            out = hook.run(pred, result, ctx)

        assert out is result

    def test_fail_open_on_exception(self):
        """svs_by_inverse_arcs raises → fail-open, result unchanged."""
        hook = CrsDacRecipientSetHook()
        pred, result = _set_pred()
        ctx = _set_ctx()

        with patch(
            "dc_search.retrieval.svs_by_inverse_arcs",
            side_effect=RuntimeError("network error"),
        ):
            out = hook.run(pred, result, ctx)

        assert out is result

    def test_crs_hook_i2_guard_no_op_after_set_hook(self):
        """CRS hook short-circuits when recipient-set hook signalled success via handled_by.

        Typed channel — the caveat ``set_valued_recipient`` is user-facing and
        must not double as inter-hook IPC.
        """
        hook = CrsDacWildcardExpansionHook()
        pred, result = _set_pred()
        # Simulate that CrsDacRecipientSetHook already fired and registered itself.
        result_handled = result.model_copy(
            update={"handled_by": frozenset({_CRS_RECIPIENT_SET_HOOK_NAME})}
        )
        ctx = _set_ctx()

        out = hook.run(pred, result_handled, ctx)

        assert out is result_handled  # no-op, sv_set preserved

    def test_crs_hook_skip_ignores_user_facing_caveat(self):
        """The skip guard reads handled_by, not caveats — caveat string is not IPC."""
        hook = CrsDacWildcardExpansionHook()
        pred, result = _set_pred(sv_set=[_AFRICA_SV])
        # Caveat present but handled_by empty: simulates a downstream renaming
        # of the caveat literal that used to silently disable the skip guard.
        result_caveat_only = result.model_copy(
            update={"caveats": ["set_valued_recipient"]}
        )
        ctx = _set_ctx(candidates=[
            StatVarFeatures(
                dcid=_AFRICA_SV,
                name="Malaria grants Africa",
                population_type=["DevelopmentFinance"],
                measured_property=["DevelopmentFinanceFlow"],
                stat_type=["measuredValue"],
                member_of=["ONE/g/DevelopmentFinance_DevelopmentFinancePurpose-DACMalariacontrol_DevelopmentFinanceRecipient-DACAfrica_DevelopmentFinanceScheme-ODAGrants"],
            )
        ])
        vg = VariableGroupInfo(
            dcid="ONE/g/DevelopmentFinance_DevelopmentFinancePurpose-DACMalariacontrol_DevelopmentFinanceRecipient-DACAfrica_DevelopmentFinanceScheme-ODAGrants",
            name="Malaria Africa",
            parents=[],
            child_groups=[],
            child_vars=[{"dcid": _AFRICA_SV, "name": "Africa Malaria"}],
        )
        with patch("dc_search.hooks.variable_group", return_value=vg):
            out = hook.run(pred, result_caveat_only, ctx)

        assert isinstance(out, AnswerCollection)
        # Hook did NOT short-circuit on the caveat alone — it ran the scalar
        # SVG path. svg_dcids being populated proves the guard didn't fire.
        assert out.svg_dcids

    def test_crs_hook_proceeds_on_set_pred_without_handled_by(self):
        """CRS hook proceeds normally when handled_by does not register the set hook.

        Fail-open path: the set hook returned result unchanged (empty family
        etc.), so handled_by stays empty and the CRS hook handles the scalar
        aggregate path.
        """
        hook = CrsDacWildcardExpansionHook()
        pred, result = _set_pred(sv_set=[_AFRICA_SV])
        ctx = _set_ctx(candidates=[
            StatVarFeatures(
                dcid=_AFRICA_SV,
                name="Malaria grants Africa",
                population_type=["DevelopmentFinance"],
                measured_property=["DevelopmentFinanceFlow"],
                stat_type=["measuredValue"],
                member_of=["ONE/g/DevelopmentFinance_DevelopmentFinancePurpose-DACMalariacontrol_DevelopmentFinanceRecipient-DACAfrica_DevelopmentFinanceScheme-ODAGrants"],
            )
        ])
        # handled_by lacks the recipient-set hook → skip guard does NOT fire → CRS hook proceeds.
        vg = VariableGroupInfo(
            dcid="ONE/g/DevelopmentFinance_DevelopmentFinancePurpose-DACMalariacontrol_DevelopmentFinanceRecipient-DACAfrica_DevelopmentFinanceScheme-ODAGrants",
            name="Malaria Africa",
            parents=[],
            child_groups=[],
            child_vars=[{"dcid": _AFRICA_SV, "name": "Africa Malaria"}],
        )
        with patch("dc_search.hooks.variable_group", return_value=vg):
            out = hook.run(pred, result, ctx)

        assert isinstance(out, AnswerCollection)
        # CRS hook produced an svg_dcid (it ran normally, not no-op'd).
        assert out.svg_dcids


# ===========================================================================
# Slice 7 — Integration: set predicate flows through materialize_via_hooks /
# materialize_many to a multi-SV AnswerCollection with set_valued_recipient.
# ===========================================================================

# Candidates for the integration test: the Africa aggregate SV is in the pool
# so the universal materializer seeds it into result.sv_set before hooks run.
_AFRICA_AGG_CANDIDATE = StatVarFeatures(
    dcid=_AFRICA_SV,
    name="Malaria grants Africa aggregate",
    population_type=["DevelopmentFinance"],
    measured_property=["DevelopmentFinanceFlow"],
    stat_type=["measuredValue"],
    constraints={
        "DevelopmentFinancePurpose": ["DAC/Malariacontrol"],
        "DevelopmentFinanceRecipient": ["DAC/Africa"],
        "DevelopmentFinanceScheme": ["ODAGrants"],
    },
)


def _integration_set_pred() -> Predicate:
    """Set-valued predicate: scalar recipient = DAC/Africa, child set = KEN + TGO."""
    return Predicate(
        population_type="DevelopmentFinance",
        measured_property="DevelopmentFinanceFlow",
        constraints={
            "DevelopmentFinancePurpose": "DAC/Malariacontrol",
            "DevelopmentFinanceRecipient": "DAC/Africa",
            "DevelopmentFinanceScheme": "ODAGrants",
        },
        constraint_sets={
            "DevelopmentFinanceRecipient": frozenset({"country/KEN", "country/TGO"})
        },
    )


class TestSlice7Integration:
    """Integration assertion: set predicate flows through materialize_via_hooks /
    materialize_many to a multi-SV AnswerCollection."""

    def test_materialize_via_hooks_set_predicate_produces_unioned_sv_set(self):
        """materialize_via_hooks with set predicate: aggregate first, per-country
        children added, set_valued_recipient caveat present."""
        pred = _integration_set_pred()
        candidates = [_AFRICA_AGG_CANDIDATE]
        ctx = HookContext(
            place_dcids=(),
            place_availability=None,
            retrieval_scores={},
            raw_candidates=tuple(candidates),
        )

        with (
            patch(
                "dc_search.retrieval.svs_by_inverse_arcs",
                return_value=_MOCK_ARCS_BASE,
            ),
            patch("dc_search.hooks.stat_var_features_batch", return_value={}),
        ):
            result = materialize_via_hooks(pred, candidates, ctx=ctx)

        assert isinstance(result, AnswerCollection)
        # Aggregate (seeded by universal materializer) must be present.
        assert _AFRICA_SV in result.sv_set
        # Per-country KEN and TGO unioned onto sv_set.
        assert _KEN_MALARIA_SV in result.sv_set
        assert _TGO_MALARIA_SV in result.sv_set
        # ZAF not in child set — suffix filter excludes it.
        assert _ZAF_MALARIA_SV not in result.sv_set
        # set_valued_recipient: semantic "contained-in expansion" signal must be present.
        assert "set_valued_recipient" in result.caveats
        # Aggregate stays first (I1: ordered_union keeps result.sv_set first).
        assert result.sv_set[0] == _AFRICA_SV

    def test_materialize_many_1tuple_set_predicate_byte_equivalent(self):
        """materialize_many 1-tuple path is byte-equivalent to materialize_via_hooks
        for a set-valued predicate (aggregate + per-country + set_valued_recipient)."""
        pred = _integration_set_pred()
        candidates = [_AFRICA_AGG_CANDIDATE]
        ctx = HookContext(
            place_dcids=(),
            place_availability=None,
            retrieval_scores={},
            raw_candidates=tuple(candidates),
        )

        with (
            patch(
                "dc_search.retrieval.svs_by_inverse_arcs",
                return_value=_MOCK_ARCS_BASE,
            ),
            patch("dc_search.hooks.stat_var_features_batch", return_value={}),
        ):
            via_hooks = materialize_via_hooks(pred, candidates, ctx=ctx)
            many = materialize_many((pred,), candidates, ctx=ctx)

        assert isinstance(via_hooks, AnswerCollection)
        assert isinstance(many, AnswerCollection)
        assert many.sv_set == via_hooks.sv_set
        assert "set_valued_recipient" in many.caveats
        assert many.caveats == via_hooks.caveats

    def test_materialize_many_1tuple_set_predicate_no_prewarm(self):
        """1-tuple set predicate does NOT trigger variable_groups_batch prewarm.

        The prewarm only fires for len(predicates) > 1. A set predicate is always
        a 1-tuple (I3 guard in slot_binding), so no extra graph fetch occurs.
        """
        pred = _integration_set_pred()
        candidates = [_AFRICA_AGG_CANDIDATE]
        ctx = HookContext(
            place_dcids=(),
            place_availability=None,
            retrieval_scores={},
            raw_candidates=tuple(candidates),
        )

        with (
            patch("dc_search.hooks.variable_groups_batch") as mock_batch,
            patch(
                "dc_search.retrieval.svs_by_inverse_arcs",
                return_value=_MOCK_ARCS_BASE,
            ),
            patch("dc_search.hooks.stat_var_features_batch", return_value={}),
        ):
            result = materialize_many((pred,), candidates, ctx=ctx)

        mock_batch.assert_not_called()
        assert isinstance(result, AnswerCollection)
        assert "set_valued_recipient" in result.caveats


# ===========================================================================
# Slice 8 — api-ux tri-state: set-bound recipient with empty donor set →
# available_at_place=None (not False), set_valued_recipient present.
# ===========================================================================


class TestSlice8AvailabilityTriState:
    """Assert that for a set-bound recipient with empty donor set,
    available_at_place is None (not False) and set_valued_recipient is present.

    The _run.py post-materialize block at lines 925-939 yields new_avail=None
    when donor_dcids is empty; materialize_many builds variables against that
    ctx.place_availability.  This verifies the contract end-to-end at the hook
    layer (place_dcids=() → available_at_place=None).
    """

    def test_set_bound_recipient_empty_donor_set_available_at_place_none(self):
        """Set-bound predicate, place_dcids=() (all places were recipients):
        resulting variables have available_at_place=None, not False."""
        pred = _integration_set_pred()
        candidates = [_AFRICA_AGG_CANDIDATE]
        # place_dcids=() simulates the post-classify state: every place was bound
        # as a recipient (scalar or set), leaving the donor set empty.
        ctx = HookContext(
            place_dcids=(),
            place_availability=frozenset({_AFRICA_SV, _KEN_MALARIA_SV, _TGO_MALARIA_SV}),
            retrieval_scores={},
            raw_candidates=tuple(candidates),
        )

        with (
            patch(
                "dc_search.retrieval.svs_by_inverse_arcs",
                return_value=_MOCK_ARCS_BASE,
            ),
            patch("dc_search.hooks.stat_var_features_batch", return_value={}),
        ):
            result = materialize_many((pred,), candidates, ctx=ctx)

        assert isinstance(result, AnswerCollection)
        assert "set_valued_recipient" in result.caveats
        # Every variable must have available_at_place=None (empty donor set).
        for var in result.variables:
            assert var.available_at_place is None, (
                f"{var.dcid}: expected available_at_place=None with empty "
                f"donor set, got {var.available_at_place!r}"
            )

    def test_set_valued_recipient_present_in_assembled_answer(self):
        """End-to-end: materialize_many for a set predicate always carries
        set_valued_recipient in the assembled AnswerCollection caveats."""
        pred = _integration_set_pred()
        candidates = [_AFRICA_AGG_CANDIDATE]
        ctx = HookContext(
            place_dcids=(),
            place_availability=None,
            retrieval_scores={},
            raw_candidates=tuple(candidates),
        )

        with (
            patch(
                "dc_search.retrieval.svs_by_inverse_arcs",
                return_value=_MOCK_ARCS_BASE,
            ),
            patch("dc_search.hooks.stat_var_features_batch", return_value={}),
        ):
            result = materialize_many((pred,), candidates, ctx=ctx)

        assert isinstance(result, AnswerCollection)
        assert "set_valued_recipient" in result.caveats


# ===========================================================================
# ProjectionEnrichmentHook tests
# ===========================================================================


from dc_search.hooks import ProjectionEnrichmentHook  # noqa: E402


def _enrichment_pred() -> Predicate:
    """Minimal DevFinance predicate with recipient bound (TGO)."""
    return Predicate(
        population_type="DevelopmentFinance",
        measured_property="amount",
        constraints={"DevelopmentFinanceRecipient": "country/TGO"},
    )


def _enrichment_ctx(
    *,
    all_resolved: tuple[str, ...] = ("country/USA", "country/TGO"),
    donor: tuple[str, ...] = ("country/USA",),
    raw_candidates: tuple[StatVarFeatures, ...] = (),
    defaulted_recipient: bool = False,
) -> HookContext:
    return HookContext(
        place_dcids=donor,
        place_availability=None,
        retrieval_scores={},
        raw_candidates=raw_candidates,
        all_resolved_dcids=all_resolved,
        defaulted_recipient=defaulted_recipient,
    )


class TestProjectionEnrichmentHook:
    """ProjectionEnrichmentHook owns the work that used to live in the
    orchestrator's post-materialize block: backup feature fetch, availability
    recompute against the donor set, variable rebuild, and the
    ``interpreted_place_as_recipient`` caveat."""

    def test_applies_always_true(self):
        """Always-applies — work is gated by ctx flags inside ``run``."""
        hook = ProjectionEnrichmentHook()
        pred = _enrichment_pred()
        assert hook.applies(pred, (), _enrichment_ctx()) is True

    def test_skip_when_donor_equals_resolved_and_no_added_svs(self):
        """No projection (donor == resolved) + no hook-added SVs → zero-cost passthrough."""
        hook = ProjectionEnrichmentHook()
        pred = _enrichment_pred()
        sv_dcid = "ONE/CRS_DAC/Malaria-ODAGrants-USA"
        candidate = StatVarFeatures(
            dcid=sv_dcid,
            name="Malaria USA",
            population_type=["DevelopmentFinance"],
            measured_property=["amount"],
        )
        ctx = _enrichment_ctx(
            all_resolved=("country/USA",),
            donor=("country/USA",),
            raw_candidates=(candidate,),
        )
        result = AnswerCollection(
            predicate=pred,
            sv_set=[sv_dcid],
            confidence="medium",
        )
        out = hook.run(pred, result, ctx)
        # No enrichment performed: variables stay as the input (empty here).
        assert out.variables == []

    def test_enriches_when_hook_added_svs_not_in_retrieval(self):
        """sv_set contains a DCID absent from retrieved features → backup fetch
        + variable rebuild populates names."""
        hook = ProjectionEnrichmentHook()
        pred = _enrichment_pred()
        added_dcid = "ONE/CRS_DAC/Malariacontrol-ODAGrants-TGO"
        added_features = StatVarFeatures(
            dcid=added_dcid,
            name="Malaria TGO grants",
            population_type=["DevelopmentFinance"],
            measured_property=["amount"],
        )
        ctx = _enrichment_ctx(
            all_resolved=("country/USA", "country/TGO"),
            donor=("country/USA",),
            raw_candidates=(),  # added_dcid absent → triggers backup fetch
        )
        result = AnswerCollection(
            predicate=pred,
            sv_set=[added_dcid],
            confidence="high",
        )

        with (
            patch("dc_search.hooks.stat_var_features_batch", return_value={
                added_dcid: added_features,
            }),
            patch(
                "dc_search.pipeline._availability._resolve_union_availability_with_ranges",
                return_value=(frozenset({added_dcid}), {added_dcid: ("2010", "2020")}, False),
            ),
        ):
            out = hook.run(pred, result, ctx)

        names = [v.name for v in out.variables]
        assert "Malaria TGO grants" in names
        # Donor non-empty + availability says SV is present at donor.
        assert all(v.available_at_place is True for v in out.variables)
        # Date range piped through from the recompute.
        assert out.variables[0].date_range is not None
        assert out.variables[0].date_range.earliest == "2010"

    def test_enriches_when_donor_narrows(self):
        """donor != all_resolved (recipient was bound) → availability recomputed
        against donor, not against the full place set."""
        hook = ProjectionEnrichmentHook()
        pred = _enrichment_pred()
        sv_dcid = "ONE/CRS_DAC/Malariacontrol-ODAGrants-TGO"
        feature = StatVarFeatures(
            dcid=sv_dcid,
            name="Malaria TGO grants",
            population_type=["DevelopmentFinance"],
            measured_property=["amount"],
        )
        ctx = _enrichment_ctx(
            all_resolved=("country/USA", "country/TGO"),  # full set
            donor=("country/USA",),                        # TGO bound as recipient
            raw_candidates=(feature,),
        )
        result = AnswerCollection(
            predicate=pred, sv_set=[sv_dcid], confidence="high",
        )

        with patch(
            "dc_search.pipeline._availability._resolve_union_availability_with_ranges",
            return_value=(frozenset({sv_dcid}), {}, False),
        ) as mock_avail:
            out = hook.run(pred, result, ctx)

        # Donor-only call (not the full place set).
        mock_avail.assert_called_once()
        args, _kwargs = mock_avail.call_args
        assert args[0] == ["country/USA"], "Availability must be recomputed against donor"
        assert out.variables[0].available_at_place is True

    def test_empty_donor_omits_availability(self):
        """All resolved places bound as recipients → donor set empty → availability
        is None (omitted), not False — matches the prior orchestrator contract."""
        hook = ProjectionEnrichmentHook()
        pred = _enrichment_pred()
        sv_dcid = "ONE/CRS_DAC/Malariacontrol-ODAGrants-NGA"
        feature = StatVarFeatures(
            dcid=sv_dcid,
            name="Malaria NGA grants",
            population_type=["DevelopmentFinance"],
            measured_property=["amount"],
        )
        ctx = _enrichment_ctx(
            all_resolved=("country/NGA",),
            donor=(),  # NGA is the recipient → no donors left
            raw_candidates=(feature,),
        )
        result = AnswerCollection(
            predicate=pred, sv_set=[sv_dcid], confidence="high",
        )

        out = hook.run(pred, result, ctx)

        assert all(v.available_at_place is None for v in out.variables)
        assert all(v.date_range is None for v in out.variables)

    def test_stamps_interpreted_place_as_recipient_caveat(self):
        """``ctx.defaulted_recipient=True`` → caveat appended idempotently."""
        hook = ProjectionEnrichmentHook()
        pred = _enrichment_pred()
        sv_dcid = "ONE/CRS_DAC/Malariacontrol-ODAGrants-NGA"
        ctx = _enrichment_ctx(
            all_resolved=("country/NGA",),
            donor=(),
            defaulted_recipient=True,
        )
        result = AnswerCollection(
            predicate=pred, sv_set=[sv_dcid], confidence="high",
        )

        out = hook.run(pred, result, ctx)
        assert "interpreted_place_as_recipient" in out.caveats

    def test_does_not_double_stamp_caveat(self):
        """Caveat already present → not duplicated."""
        hook = ProjectionEnrichmentHook()
        pred = _enrichment_pred()
        sv_dcid = "ONE/CRS_DAC/Malariacontrol-ODAGrants-NGA"
        ctx = _enrichment_ctx(
            all_resolved=("country/NGA",),
            donor=(),
            defaulted_recipient=True,
        )
        result = AnswerCollection(
            predicate=pred,
            sv_set=[sv_dcid],
            confidence="high",
            caveats=["interpreted_place_as_recipient"],
        )

        out = hook.run(pred, result, ctx)
        assert out.caveats.count("interpreted_place_as_recipient") == 1

    def test_no_caveat_when_defaulted_recipient_false(self):
        """Explicit role ('grants from us to togo') → no caveat."""
        hook = ProjectionEnrichmentHook()
        pred = _enrichment_pred()
        sv_dcid = "ONE/CRS_DAC/Malariacontrol-ODAGrants-TGO"
        ctx = _enrichment_ctx(defaulted_recipient=False)
        result = AnswerCollection(
            predicate=pred, sv_set=[sv_dcid], confidence="high",
        )

        out = hook.run(pred, result, ctx)
        assert "interpreted_place_as_recipient" not in out.caveats

    def test_patches_availability_when_upstream_built_variables(self):
        """Recovery hook produced complete variables (correct names) but its
        availability was computed against the pre-donor place set.  When the
        donor set differs, ProjectionEnrichmentHook must re-derive
        ``available_at_place`` from the donor-narrow availability and patch
        the existing variables in place — preserving names, replacing
        availability + date_range only.

        Regression test for a real bug: Piece D recovery + donor narrowing
        previously skipped the recompute entirely, leaving stale availability."""
        hook = ProjectionEnrichmentHook()
        pred = _enrichment_pred()
        sv_dcid = "ONE/CRS_DAC/Malariacontrol-ODAGrants-TGO"
        # Upstream hook (e.g. CrsDacRetrievalRecoveryHook) populated variables
        # with names already, but the availability value reflects the WRONG
        # place set: True against the union including the bound recipient.
        upstream_variables = [
            ResolvedVariable(
                dcid=sv_dcid,
                name="Malaria TGO grants",  # name set by upstream's fetch
                available_at_place=True,    # stale: union(USA, TGO) availability
            )
        ]
        ctx = _enrichment_ctx(
            all_resolved=("country/USA", "country/TGO"),
            donor=("country/USA",),
            raw_candidates=(),  # SV not in retrieval pool (Piece D scenario)
        )
        result = AnswerCollection(
            predicate=pred,
            sv_set=[sv_dcid],
            confidence="high",
            variables=upstream_variables,
        )

        # Donor-set availability says the SV is NOT available at USA alone.
        with (
            patch(
                "dc_search.pipeline._availability._resolve_union_availability_with_ranges",
                return_value=(frozenset(), {}, False),
            ),
            patch(
                "dc_search.hooks.stat_var_features_batch",
            ) as mock_fetch,
        ):
            out = hook.run(pred, result, ctx)

        # Name preserved from upstream — no redundant feature fetch.
        assert out.variables[0].name == "Malaria TGO grants"
        mock_fetch.assert_not_called()
        # Availability re-derived against the donor set: empty avail → False.
        assert out.variables[0].available_at_place is False

    def test_backup_fetch_failure_is_fail_open(self):
        """stat_var_features_batch raises → names remain None, no crash."""
        hook = ProjectionEnrichmentHook()
        pred = _enrichment_pred()
        added_dcid = "ONE/CRS_DAC/Malariacontrol-ODAGrants-TGO"
        ctx = _enrichment_ctx(
            all_resolved=("country/USA", "country/TGO"),
            donor=("country/USA",),
            raw_candidates=(),
        )
        result = AnswerCollection(
            predicate=pred, sv_set=[added_dcid], confidence="high",
        )

        with (
            patch(
                "dc_search.hooks.stat_var_features_batch",
                side_effect=RuntimeError("transient mixer error"),
            ),
            patch(
                "dc_search.pipeline._availability._resolve_union_availability_with_ranges",
                return_value=(frozenset(), {}, False),
            ),
        ):
            out = hook.run(pred, result, ctx)

        # Variable still constructed (the projection ran) but name is None.
        assert len(out.variables) == 1
        assert out.variables[0].dcid == added_dcid
        assert out.variables[0].name is None
