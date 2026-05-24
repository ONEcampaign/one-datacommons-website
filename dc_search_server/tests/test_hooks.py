"""Tests for the hook pipeline (hooks.py)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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
# DateFilterHook tests (scaffold — no-op pass-through)
# ===========================================================================


def _ctx_with_dates(dates: list[ExtractedDate]) -> HookContext:
    return HookContext(
        place_dcids=(),
        place_availability=None,
        retrieval_scores={},
        raw_candidates=(),
        dates=dates,
    )


def test_date_filter_hook_applies_when_dates_present() -> None:
    """DateFilterHook.applies() returns True when ctx.dates is non-empty."""
    hook = DateFilterHook()
    pred = _census_predicate()
    dates = [ExtractedDate(kind="range", start="2010", end=None)]
    ctx = _ctx_with_dates(dates)

    assert hook.applies(pred, (), ctx) is True


def test_date_filter_hook_does_not_apply_when_dates_empty() -> None:
    """DateFilterHook.applies() returns False when ctx.dates is empty."""
    hook = DateFilterHook()
    pred = _census_predicate()
    ctx = _ctx_with_dates([])

    assert hook.applies(pred, (), ctx) is False


def test_date_filter_hook_run_is_no_op(caplog) -> None:
    """DateFilterHook.run() returns all candidates unchanged and logs the dates."""
    import logging

    hook = DateFilterHook()
    pred = _census_predicate()
    sv_set = ["SV_A", "SV_B", "SV_C"]
    result = _answer(sv_set)
    dates = [
        ExtractedDate(kind="point", start="2020", end=None),
        ExtractedDate(kind="range", start="2015", end="2020"),
    ]
    ctx = _ctx_with_dates(dates)

    with caplog.at_level(logging.INFO, logger="dc_search.hooks"):
        out = hook.run(pred, result, ctx)

    assert isinstance(out, AnswerCollection)
    assert out.sv_set == sv_set
    assert out is result
    assert any("DateFilterHook" in r.message for r in caplog.records)
