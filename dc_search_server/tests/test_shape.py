"""Tests for dc_search.shape — namespace classification, shape grouping,
and place-token extraction.

All tests are pure unit tests — no network calls, no LLM calls.
Shared fixtures (crs_dac_candidates, census_candidates, sdg_candidates,
who_candidates, mixed_candidates) come from tests/conftest.py.
"""

from __future__ import annotations

import pytest

import dc_search.retrieval
import dc_search.shape as _shape_mod
from dc_search.retrieval import StatVarFeatures
from dc_search.shape import (
    ShapeContext,
    build_shape_context,
    classify_namespace,
    extract_place_tokens,
)

# ---------------------------------------------------------------------------
# classify_namespace
# ---------------------------------------------------------------------------


def test_classify_namespace_crs_dac() -> None:
    """ONE/CRS_DAC/ prefix → "CRS_DAC"."""
    assert classify_namespace("ONE/CRS_DAC/STDcontrolincludingHIVAIDS-ODAGrants-ZAF") == "CRS_DAC"
    assert classify_namespace("ONE/CRS_DAC/Health-ODAGrants-F") == "CRS_DAC"


def test_classify_namespace_census() -> None:
    """Count_*, Median_*, Mortality_*, LifeExpectancy_*, Amount_*, Mean_*, Percent_* → "Census"."""
    assert classify_namespace("Count_Person") == "Census"
    assert classify_namespace("Count_Person_Female") == "Census"
    assert classify_namespace("Count_MortalityEvent_Cause-HIVAIDS") == "Census"
    assert classify_namespace("Count_MedicalConditionIncident_ConditionHIVAIDS") == "Census"
    assert classify_namespace("Median_Age_Person") == "Census"
    assert classify_namespace("Mortality_Rate_Under5") == "Census"
    assert classify_namespace("LifeExpectancy_Person") == "Census"
    assert classify_namespace("Amount_EconomicActivity_GrossDomesticProduction") == "Census"
    assert classify_namespace("Mean_Income_Person") == "Census"
    assert classify_namespace("Percent_Person_Employed") == "Census"


def test_classify_namespace_sdg() -> None:
    """sdg/ prefix → "SDG"."""
    assert classify_namespace("sdg/SH_STA_MORT") == "SDG"
    assert classify_namespace("sdg/SG_GEN_PARL") == "SDG"


def test_classify_namespace_who() -> None:
    """ONE/who_* and WHO/ prefixes → "WHO"."""
    assert classify_namespace("ONE/who_dis13") == "WHO"
    assert classify_namespace("ONE/who_CM_03") == "WHO"
    assert classify_namespace("WHO/someIndicator") == "WHO"


def test_classify_namespace_other() -> None:
    """Unknown prefixes → "Other"."""
    assert classify_namespace("SomeRandomDCID") == "Other"
    assert classify_namespace("dc/SomeNode") == "Other"
    assert classify_namespace("") == "Other"
    assert classify_namespace("Topic/HIV") == "Other"


# ---------------------------------------------------------------------------
# build_shape_context — grouping
# ---------------------------------------------------------------------------


def test_build_shape_context_groups_by_fingerprint(
    mixed_candidates: list[StatVarFeatures],
) -> None:
    """Mixed candidates produce multiple shapes, one per (namespace, popType,
    measProp, constraintKeys) fingerprint."""
    ctx = build_shape_context("HIV deaths in South Africa", mixed_candidates)
    assert isinstance(ctx, ShapeContext)
    # There should be multiple shapes — the mixed set spans several namespaces
    # and population types.
    assert len(ctx.shapes) > 1
    # Every shape has at least one member.
    for shape in ctx.shapes:
        assert len(shape.member_dcids) >= 1
    # All member DCIDs are a subset of the input DCIDs.
    input_dcids = {svf.dcid for svf in mixed_candidates}
    for shape in ctx.shapes:
        for dcid in shape.member_dcids:
            assert dcid in input_dcids


def test_build_shape_context_largest_shape_first(
    crs_dac_candidates: list[StatVarFeatures],
    census_candidates: list[StatVarFeatures],
) -> None:
    """Shapes are ordered by member count, largest first."""
    ctx = build_shape_context("grants", [*crs_dac_candidates, *census_candidates])
    for i in range(len(ctx.shapes) - 1):
        assert len(ctx.shapes[i].member_dcids) >= len(ctx.shapes[i + 1].member_dcids)


def test_build_shape_context_slot_taxonomy_union(
    crs_dac_candidates: list[StatVarFeatures],
) -> None:
    """For the CRS_DAC shape with 3 members, slot_taxonomy contains the union
    of all constraint values across the 3 SVs."""
    ctx = build_shape_context("grants for malaria", crs_dac_candidates)
    assert len(ctx.shapes) == 1  # all 3 share the same fingerprint
    shape = ctx.shapes[0]

    # Purpose slot should contain all three purpose values.
    assert "DevelopmentFinancePurpose" in shape.slot_taxonomy
    purposes = set(shape.slot_taxonomy["DevelopmentFinancePurpose"])
    assert "DAC/STDcontrolincludingHIVAIDS" in purposes
    assert "DAC/Malariacontrol" in purposes
    assert "DAC/Health" in purposes

    # Recipient slot should contain all three recipient values.
    assert "DevelopmentFinanceRecipient" in shape.slot_taxonomy
    recipients = set(shape.slot_taxonomy["DevelopmentFinanceRecipient"])
    assert "country/ZAF" in recipients
    assert "country/KEN" in recipients
    assert "DAC/Africa" in recipients

    # Scheme slot: all three SVs have ODAGrants.
    assert "DevelopmentFinanceScheme" in shape.slot_taxonomy
    assert "ODAGrants" in shape.slot_taxonomy["DevelopmentFinanceScheme"]


def test_build_shape_context_filters_empty_population_type() -> None:
    """Only candidates with BOTH populationType AND measuredProperty empty
    are dropped. Candidates that carry at least one of the two are kept —
    WHO indicator codes (e.g. ONE/who_hf3) declare populationType but no
    measuredProperty, and must still produce a usable shape.
    """
    good = StatVarFeatures(
        dcid="Count_Person",
        population_type=["Person"],
        measured_property=["count"],
    )
    no_pop = StatVarFeatures(
        dcid="Topic_HIV",
        population_type=[],
        measured_property=["count"],
    )
    no_meas = StatVarFeatures(
        dcid="SomeSV",
        population_type=["Person"],
        measured_property=[],
    )
    # A non-Topic DCID with no populationType or measuredProperty is still dropped.
    truly_empty = StatVarFeatures(
        dcid="UnknownSV_NoProps",
        population_type=[],
        measured_property=[],
    )
    # A Topic DCID with no populationType or measuredProperty is KEPT (Topic path).
    topic_empty = StatVarFeatures(
        dcid="dc/topic/SomeRollupContainer",
        population_type=[],
        measured_property=[],
    )
    ctx = build_shape_context("test query", [good, no_pop, no_meas, truly_empty, topic_empty])
    all_dcids = {dcid for shape in ctx.shapes for dcid in shape.member_dcids}
    assert "Count_Person" in all_dcids
    # Either-but-not-both: kept (WHO-style and other partially-typed SVs).
    assert "Topic_HIV" in all_dcids
    assert "SomeSV" in all_dcids
    # Non-Topic DCID with no grouping signal: dropped.
    assert "UnknownSV_NoProps" not in all_dcids
    # Topic DCID: kept even without populationType/measuredProperty.
    assert "dc/topic/SomeRollupContainer" in all_dcids


def test_build_shape_context_empty_candidates() -> None:
    """Empty candidate list produces ShapeContext with no shapes."""
    ctx = build_shape_context("some query", [])
    assert ctx.shapes == ()
    assert ctx.query == "some query"


# ---------------------------------------------------------------------------
# Keyword cue extraction
# ---------------------------------------------------------------------------


def test_keyword_cues_place_dcids(
    monkeypatch: pytest.MonkeyPatch,
    mixed_candidates: list[StatVarFeatures],
) -> None:
    """Place mentions are resolved to DCIDs in keyword_cues['place_dcids'].

    Uses a monkeypatched resolve_place so the test is network-free.
    """
    monkeypatch.setattr(
        _shape_mod.graph,
        "resolve_place",
        lambda *, name: (
            (dc_search.retrieval.PlaceCandidate(dcid="country/ZAF"),)
            if name.lower() == "south africa"
            else ()
        ),
    )
    _shape_mod._extract_place_tokens_cache.clear()

    ctx = build_shape_context("grants in South Africa for malaria", mixed_candidates)
    assert ctx.keyword_cues["place_dcids"] == ["country/ZAF"]


def test_build_shape_context_crs_dac_single_shape(
    crs_dac_candidates: list[StatVarFeatures],
) -> None:
    """CRS_DAC candidates all share one fingerprint → exactly one shape.

    Identified by populationType == DevelopmentFinance (no namespace label on Shape).
    """
    ctx = build_shape_context("grants for HIV/AIDS in South Africa", crs_dac_candidates)
    assert len(ctx.shapes) == 1
    shape = ctx.shapes[0]
    assert shape.population_type == "DevelopmentFinance"
    assert len(shape.member_dcids) == 3


def test_build_shape_context_sdg_each_sv_is_own_shape(
    sdg_candidates: list[StatVarFeatures],
) -> None:
    """SDG candidates each have unique populationType → one shape per SV.

    Each SDG SV has a unique populationType (e.g. SDG_SH_STA_MORT),
    so 2 candidates → 2 shapes.
    """
    ctx = build_shape_context("maternal mortality SDG", sdg_candidates)
    # 2 SDG candidates, each with distinct populationType → 2 shapes.
    assert len(ctx.shapes) == 2
    # Each shape's populationType starts with "SDG_" (from conftest fixtures).
    for shape in ctx.shapes:
        assert shape.population_type is not None
        assert shape.population_type.startswith("SDG_")


def test_build_shape_context_query_preserved(
    census_candidates: list[StatVarFeatures],
) -> None:
    """ShapeContext.query matches the input query exactly."""
    q = "total population by gender"
    ctx = build_shape_context(q, census_candidates)
    assert ctx.query == q


# ---------------------------------------------------------------------------
# extract_place_tokens — unit tests (all network-free via monkeypatched
# resolve_place)
# ---------------------------------------------------------------------------

# Each test uses a local "gazetteer" dict {lowercased_name: dcid} and a
# helper that installs it as the mock.


def _install_gazetteer(
    monkeypatch: pytest.MonkeyPatch,
    gazetteer: dict[str, str],
) -> None:
    """Install a fake resolve_place backed by *gazetteer* and clear the cache."""

    def _mock(*, name: str) -> tuple[dc_search.retrieval.PlaceCandidate, ...]:
        dcid = gazetteer.get(name.lower())
        if dcid is None:
            return ()
        return (dc_search.retrieval.PlaceCandidate(dcid=dcid),)

    monkeypatch.setattr(_shape_mod.graph, "resolve_place", _mock)
    _shape_mod._extract_place_tokens_cache.clear()


def test_ept_basic_title_case(monkeypatch: pytest.MonkeyPatch) -> None:
    """Title-case single token resolves correctly."""
    _install_gazetteer(monkeypatch, {"togo": "country/TGO"})
    assert extract_place_tokens("total population of Togo") == ["country/TGO"]


def test_ept_lowercase_single(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lowercase single token resolves when not on stop-list."""
    _install_gazetteer(monkeypatch, {"guatemala": "country/GTM"})
    assert extract_place_tokens("guatemala population") == ["country/GTM"]


def test_ept_all_caps_iso3(monkeypatch: pytest.MonkeyPatch) -> None:
    """All-caps ISO-3 code resolves."""
    _install_gazetteer(monkeypatch, {"zaf": "country/ZAF"})
    assert extract_place_tokens("ZAF maternal mortality") == ["country/ZAF"]


def test_ept_multi_word_lowercase(monkeypatch: pytest.MonkeyPatch) -> None:
    """Multi-word lowercase place resolved via 2-gram."""
    _install_gazetteer(monkeypatch, {"south africa": "country/ZAF"})
    assert extract_place_tokens("deaths in south africa") == ["country/ZAF"]


def test_ept_no_place_neonatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Query with only stop-listed tokens → empty result, resolve_place never called."""
    called: list[str] = []

    def _spy(*, name: str) -> tuple[dc_search.retrieval.PlaceCandidate, ...]:
        called.append(name)
        return ()

    monkeypatch.setattr(_shape_mod.graph, "resolve_place", _spy)
    _shape_mod._extract_place_tokens_cache.clear()
    result = extract_place_tokens("neonatal mortality rate")
    assert result == []
    assert called == []


def test_ept_grants_stoplist(monkeypatch: pytest.MonkeyPatch) -> None:
    """'grants' is on the stop-list → never passed as a 1-gram to resolve_place."""
    called: list[str] = []

    def _spy(*, name: str) -> tuple[dc_search.retrieval.PlaceCandidate, ...]:
        called.append(name)
        return ()

    monkeypatch.setattr(_shape_mod.graph, "resolve_place", _spy)
    _shape_mod._extract_place_tokens_cache.clear()
    result = extract_place_tokens("grants for malaria control")
    assert result == []
    # The 1-gram "grants" is stop-listed and must never appear alone.
    assert "grants" not in called


def test_ept_multi_place_canada_us_south_africa(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Three places extracted from one query, grants/HIV/AIDS stop-listed."""
    _install_gazetteer(
        monkeypatch,
        {
            "canada": "country/CAN",
            "us": "country/USA",
            "south africa": "country/ZAF",
        },
    )
    result = extract_place_tokens("Canada and US grants for HIV/AIDS in South Africa")
    assert set(result) == {"country/CAN", "country/USA", "country/ZAF"}


def test_ept_dedup_by_dcid(monkeypatch: pytest.MonkeyPatch) -> None:
    """Multiple aliases resolving to same DCID → deduplicated to one entry."""
    _install_gazetteer(
        monkeypatch,
        {
            "us": "country/USA",
            "usa": "country/USA",
            "united states": "country/USA",
        },
    )
    result = extract_place_tokens("US and USA and United States")
    assert result == ["country/USA"]  # exactly one entry


def test_ept_empty_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty query → empty result."""
    _install_gazetteer(monkeypatch, {})
    assert extract_place_tokens("") == []


def test_ept_punctuation_split(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forward-slash splits tokens; 'HIV' and 'AIDS' are stop-listed."""
    _install_gazetteer(monkeypatch, {"south africa": "country/ZAF"})
    result = extract_place_tokens("HIV/AIDS in South Africa")
    assert result == ["country/ZAF"]


def test_ept_span_maximality(monkeypatch: pytest.MonkeyPatch) -> None:
    """Longer span wins; shorter overlapping span is dropped."""
    _install_gazetteer(
        monkeypatch,
        {
            "republic of korea": "country/KOR",
            "korea": "country/KOR",
        },
    )
    result = extract_place_tokens("tb incidence in Republic of Korea")
    assert result == ["country/KOR"]  # one entry, not two


def test_ept_resolve_failure_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    """resolve_place raising for one candidate does not crash; others still resolve."""
    france_dcid = "country/FRA"
    kenya_dcid = "country/KEN"

    def _mock(*, name: str) -> tuple[dc_search.retrieval.PlaceCandidate, ...]:
        if name.lower() == "france":
            return (dc_search.retrieval.PlaceCandidate(dcid=france_dcid),)
        if name.lower() == "atlantis":
            raise RuntimeError("resolve_place network error")
        if name.lower() == "kenya":
            return (dc_search.retrieval.PlaceCandidate(dcid=kenya_dcid),)
        return ()

    monkeypatch.setattr(_shape_mod.graph, "resolve_place", _mock)
    _shape_mod._extract_place_tokens_cache.clear()
    result = extract_place_tokens("France Atlantis Kenya")
    assert france_dcid in result
    assert kenya_dcid in result


# ---------------------------------------------------------------------------
# Topic namespace classification and shape grouping
# ---------------------------------------------------------------------------


def test_classify_namespace_topic_dc_prefix() -> None:
    """dc/topic/* prefix → "Topic"."""
    assert classify_namespace("dc/topic/DecadeProjectedTemperatureHighs") == "Topic"
    assert classify_namespace("dc/topic/GDP") == "Topic"
    assert classify_namespace("dc/topic/ProjectedClimateExtremes") == "Topic"


def test_classify_namespace_topic_one_prefix() -> None:
    """ONE/topic/* prefix → "Topic"."""
    assert classify_namespace("ONE/topic/HealthFinancing-ODAGrants") == "Topic"
    assert classify_namespace("ONE/topic/CurrentHealthExpenditure") == "Topic"


def test_classify_namespace_topic_prefix_before_crs_dac() -> None:
    """Topic prefix is checked before CRS_DAC — no dc/topic/* can be CRS_DAC."""
    assert classify_namespace("ONE/topic/SomeTopic") != "CRS_DAC"


def test_build_shape_context_one_topic_per_shape() -> None:
    """Multiple Topic candidates each produce their own Shape (not collapsed)."""
    topic_a = StatVarFeatures(
        dcid="dc/topic/DecadeProjectedTemperatureHighs",
        name="Decade Projected Temperature Highs",
        population_type=[],
        measured_property=[],
    )
    topic_b = StatVarFeatures(
        dcid="dc/topic/GDP",
        name="GDP",
        population_type=[],
        measured_property=[],
    )
    ctx = build_shape_context("projected temperature", [topic_a, topic_b])
    topic_shapes = [s for s in ctx.shapes if s.is_topic]
    assert len(topic_shapes) == 2
    topic_dcids = {s.member_dcids[0] for s in topic_shapes}
    assert topic_dcids == {
        "dc/topic/DecadeProjectedTemperatureHighs",
        "dc/topic/GDP",
    }


def test_build_shape_context_topic_with_empty_population_type() -> None:
    """Topic candidates (no populationType/measuredProperty) pass the filter."""
    topic = StatVarFeatures(
        dcid="dc/topic/MaxTemperature95Pct",
        name="Max Temperature 95th percentile",
        population_type=[],
        measured_property=[],
    )
    ctx = build_shape_context("temperature predictions", [topic])
    all_dcids = {dcid for shape in ctx.shapes for dcid in shape.member_dcids}
    assert "dc/topic/MaxTemperature95Pct" in all_dcids


def test_build_shape_context_topic_shape_has_no_slot_taxonomy() -> None:
    """Topic shapes have empty slot_taxonomy, empty constraint_keys, and is_topic=True."""
    topic = StatVarFeatures(
        dcid="ONE/topic/HealthFinancing-ODAGrants",
        population_type=[],
        measured_property=[],
    )
    ctx = build_shape_context("health financing", [topic])
    topic_shapes = [s for s in ctx.shapes if s.is_topic]
    assert len(topic_shapes) == 1
    shape = topic_shapes[0]
    assert shape.slot_taxonomy == {}
    assert shape.constraint_keys == ()
    assert shape.population_type is None
    assert shape.measured_property is None


def test_gdp_shapes_collapse_to_one_shape() -> None:
    """GDP SVs sharing the same fingerprint collapse into one Shape."""
    gdp_census = StatVarFeatures(
        dcid="Amount_EconomicActivity_GrossDomesticProduction",
        name="GDP (Census)",
        population_type=["EconomicActivity"],
        measured_property=["amount"],
        stat_type=["measuredValue"],
    )
    gdp_census_ppp = StatVarFeatures(
        dcid="Amount_EconomicActivity_GrossDomesticProduction_PurchasingPowerParity",
        name="GDP PPP (Census)",
        population_type=["EconomicActivity"],
        measured_property=["amount"],
        stat_type=["measuredValue"],
    )
    ctx = build_shape_context("GDP", [gdp_census, gdp_census_ppp])
    assert len(ctx.shapes) == 1
    shape = ctx.shapes[0]
    assert shape.population_type == "EconomicActivity"
    assert set(shape.member_dcids) == {
        "Amount_EconomicActivity_GrossDomesticProduction",
        "Amount_EconomicActivity_GrossDomesticProduction_PurchasingPowerParity",
    }


# ---------------------------------------------------------------------------
# ShapeContext.resolved_places — round-trip and default
# ---------------------------------------------------------------------------


def test_build_shape_context_resolved_places_round_trips(
    census_candidates: list[StatVarFeatures],
) -> None:
    """resolved_places kwarg is stored verbatim on the returned ShapeContext."""
    places: tuple[tuple[str, str | None, str | None, str], ...] = (
        ("country/TGO", "Togo", "Togo", "recipient"),
    )
    ctx = build_shape_context(
        "grants to Togo",
        census_candidates,
        resolved_places=places,
    )
    assert ctx.resolved_places == (("country/TGO", "Togo", "Togo", "recipient"),)


def test_build_shape_context_resolved_places_default_empty(
    census_candidates: list[StatVarFeatures],
) -> None:
    """When resolved_places is omitted, ShapeContext.resolved_places defaults to ()."""
    ctx = build_shape_context("malaria deaths", census_candidates)
    assert ctx.resolved_places == ()


def test_build_shape_context_resolved_places_none_fields(
    census_candidates: list[StatVarFeatures],
) -> None:
    """resolved_places entries may carry None for canonical_name and input_surface."""
    places: tuple[tuple[str, str | None, str | None, str], ...] = (
        ("country/TGO", None, None, "ambiguous"),
    )
    ctx = build_shape_context(
        "grants to Togo",
        census_candidates,
        resolved_places=places,
    )
    assert ctx.resolved_places == (("country/TGO", None, None, "ambiguous"),)


def test_build_shape_context_resolved_places_multiple(
    census_candidates: list[StatVarFeatures],
) -> None:
    """Multiple (dcid, canonical_name, input_surface, role) 4-tuples stored in input order."""
    places: tuple[tuple[str, str | None, str | None, str], ...] = (
        ("country/USA", "United States", "us", "donor"),
        ("country/TGO", "Togo", "Togo", "recipient"),
    )
    ctx = build_shape_context(
        "grants from us to Togo",
        census_candidates,
        resolved_places=places,
    )
    assert ctx.resolved_places == (
        ("country/USA", "United States", "us", "donor"),
        ("country/TGO", "Togo", "Togo", "recipient"),
    )


# ---------------------------------------------------------------------------
# build_shape_context — max_shapes cap (shapecap10)
# ---------------------------------------------------------------------------


def test_build_shape_context_max_shapes_truncates(
    sdg_candidates: list[StatVarFeatures],
) -> None:
    """max_shapes caps the returned shapes (applied after the sort)."""
    full = build_shape_context("maternal mortality SDG", sdg_candidates)
    assert len(full.shapes) >= 2  # each SDG SV is its own shape
    capped = build_shape_context("maternal mortality SDG", sdg_candidates, max_shapes=1)
    assert len(capped.shapes) == 1
    # Truncation-after-sort: the kept shape is the first of the uncapped order.
    assert capped.shapes[0].member_dcids == full.shapes[0].member_dcids


def test_build_shape_context_max_shapes_none_returns_all(
    sdg_candidates: list[StatVarFeatures],
) -> None:
    """max_shapes=None (the default) returns every shape — no behaviour change."""
    full = build_shape_context("maternal mortality SDG", sdg_candidates)
    explicit = build_shape_context("maternal mortality SDG", sdg_candidates, max_shapes=None)
    assert len(explicit.shapes) == len(full.shapes)


def test_build_shape_context_max_shapes_above_total_returns_all(
    sdg_candidates: list[StatVarFeatures],
) -> None:
    """A cap larger than the shape count returns all shapes."""
    full = build_shape_context("maternal mortality SDG", sdg_candidates)
    capped = build_shape_context("maternal mortality SDG", sdg_candidates, max_shapes=999)
    assert len(capped.shapes) == len(full.shapes)


def test_build_shape_context_max_shapes_keeps_highest_scored() -> None:
    """With retrieval_scores, the cap keeps the top-scored shapes, not arbitrary ones."""
    low = StatVarFeatures(
        dcid="sdg/LOW",
        name="low",
        population_type=["SDG_LOW"],
        measured_property=["value"],
    )
    high = StatVarFeatures(
        dcid="sdg/HIGH",
        name="high",
        population_type=["SDG_HIGH"],
        measured_property=["value"],
    )
    ctx = build_shape_context(
        "q",
        [low, high],
        retrieval_scores={"sdg/LOW": 0.10, "sdg/HIGH": 0.99},
        max_shapes=1,
    )
    assert len(ctx.shapes) == 1
    assert ctx.shapes[0].member_dcids == ("sdg/HIGH",)


# ---------------------------------------------------------------------------
# ShapeContext.contained_in + parent_to_children — round-trip and defaults
# ---------------------------------------------------------------------------


def test_build_shape_context_contained_in_and_parent_to_children_round_trip(
    census_candidates: list[StatVarFeatures],
) -> None:
    """contained_in=True and a non-empty parent_to_children are stored verbatim."""
    p2c: dict[str, tuple[tuple[str, str | None], ...]] = {
        "DAC/Africa": (
            ("country/KEN", "Kenya"),
            ("country/TGO", "Togo"),
        ),
    }
    ctx = build_shape_context(
        "malaria grants to african countries",
        census_candidates,
        contained_in=True,
        parent_to_children=p2c,
    )
    assert ctx.contained_in is True
    assert ctx.parent_to_children == p2c


def test_build_shape_context_contained_in_defaults_false(
    census_candidates: list[StatVarFeatures],
) -> None:
    """Existing callers omitting contained_in get contained_in=False."""
    ctx = build_shape_context("malaria deaths", census_candidates)
    assert ctx.contained_in is False


def test_build_shape_context_parent_to_children_defaults_empty(
    census_candidates: list[StatVarFeatures],
) -> None:
    """Existing callers omitting parent_to_children get an empty dict."""
    ctx = build_shape_context("malaria deaths", census_candidates)
    assert ctx.parent_to_children == {}


def test_build_shape_context_contained_in_false_with_empty_p2c(
    census_candidates: list[StatVarFeatures],
) -> None:
    """contained_in=False and parent_to_children={} are stored correctly (explicit defaults)."""
    ctx = build_shape_context(
        "malaria grants to Nigeria",
        census_candidates,
        contained_in=False,
        parent_to_children={},
    )
    assert ctx.contained_in is False
    assert ctx.parent_to_children == {}
