"""Shared test fixtures — shared across the test suite."""

import os

os.environ.setdefault("GOOGLE_API_KEY", "test-key-not-used-by-mock")
os.environ.setdefault("GEMINI_API_KEY", "test-key-not-used-by-mock")

from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _reset_dc_search_logging() -> None:
    """Restore the dc_search logger to a caplog-capturable state before each test.

    ``src/dc_search/app.py`` calls ``logging.getLogger("dc_search")`` and sets
    ``propagate = False`` plus attaches a StreamHandler so production logs go
    directly to stdout without duplicating to the root logger.  When test_app.py
    exercises that code path the mutation persists globally (Python's logging
    hierarchy is process-wide), and pytest's ``caplog`` fixture — which captures
    by injecting a handler at the root logger and relying on propagation — stops
    seeing any records emitted under the ``dc_search.*`` hierarchy.

    This fixture undoes that mutation before each test so caplog works everywhere.
    The ``app.py`` behavior itself is correct for production; only its effect on
    test isolation needs to be neutralised here.
    """
    import logging

    lg = logging.getLogger("dc_search")
    lg.propagate = True
    for h in lg.handlers[:]:
        lg.removeHandler(h)


@pytest.fixture(autouse=True)
def _clear_module_caches() -> None:
    """Clear all module-level LRU caches between tests."""

    # Clear shape module's LRU cache
    try:
        import dc_search.shape as _shape

        _shape._extract_place_tokens_cache.clear()
    except (ImportError, AttributeError):
        pass

    # Clear retrieval module's LRU caches
    try:
        from dc_search import retrieval as _retrieval

        for cache_name in (
            "_resolve_cache",
            "_features_cache",
            "_entity_svs_cache",
            "_presence_cache",
            "_coverage_cache",
            "_variable_info_dates_cache",
            "_observation_dates_cache",
            "_observation_facet_ranges_cache",
            "_vgroups_cache",
            "_topic_arc_cache",
            "_place_names_cache",
        ):
            cache = getattr(_retrieval, cache_name, None)
            if cache is not None and hasattr(cache, "clear"):
                cache.clear()
        # The fail-open degraded flag is a ContextVar; tests run synchronously in
        # one context (no asyncio.to_thread copy), so a set() in one test would
        # otherwise leak into the next.
        _retrieval.reset_dc_call_degraded()
    except (ImportError, AttributeError):
        pass


@pytest.fixture(autouse=True)
def _default_resolve_place_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch place-resolution to return empty by default for all tests.

    Patched via the ``shape`` module's ``graph`` attribute so that only code
    going through ``shape.graph.*`` is affected.  Direct callers of
    ``retrieval.resolve_places_batch`` (e.g. test_retrieval.py) are NOT
    affected — this avoids the fixture replacing ``retrieval.resolve_places_batch``
    globally and breaking retrieval-level cache tests.

    Tests that need custom place resolution override this with their own
    monkeypatch.setattr call (last setattr wins).
    """
    try:
        import types

        import dc_search.shape as _shape
        from dc_search.retrieval import PlaceCandidate

        # Build a lightweight proxy that delegates everything to the real
        # retrieval module, overriding only the two place-resolution functions.
        # Patching _shape.graph (not retrieval directly) means test_retrieval.py's
        # direct calls to retrieval.resolve_places_batch are unaffected.
        real_graph = _shape.graph

        stub_graph = types.SimpleNamespace(
            **{k: getattr(real_graph, k) for k in dir(real_graph) if not k.startswith("__")}
        )
        stub_graph.resolve_place = lambda *, name: ()

        def _stub_batch(*, names: tuple[str, ...]) -> dict[str, tuple[PlaceCandidate, ...]]:
            # Delegate through stub_graph.resolve_place so that individual tests
            # that override stub_graph.resolve_place see their override honored.
            out: dict[str, tuple[PlaceCandidate, ...]] = {}
            for n in names:
                try:
                    out[n] = stub_graph.resolve_place(name=n)
                except Exception:
                    out[n] = ()
            return out

        stub_graph.resolve_places_batch = _stub_batch

        monkeypatch.setattr(_shape, "graph", stub_graph)
    except (ImportError, AttributeError):
        pass


@pytest.fixture
def genai_client():
    return MagicMock()


@pytest.fixture
def dc_client():
    return MagicMock()


# ---------------------------------------------------------------------------
# Shared candidate-set fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def crs_dac_candidates():
    from dc_search.retrieval import StatVarFeatures

    return [
        StatVarFeatures(
            dcid="ONE/CRS_DAC/STDcontrolincludingHIVAIDS-ODAGrants-ZAF",
            name="STD control including HIV/AIDS [Grants to South Africa]",
            description="ODA grants to South Africa for HIV/AIDS control.",
            population_type=["DevelopmentFinance"],
            measured_property=["DevelopmentFinanceFlow"],
            stat_type=["measuredValue"],
            constraints={
                "DevelopmentFinancePurpose": ["DAC/STDcontrolincludingHIVAIDS"],
                "DevelopmentFinanceRecipient": ["country/ZAF"],
                "DevelopmentFinanceScheme": ["ODAGrants"],
            },
        ),
        StatVarFeatures(
            dcid="ONE/CRS_DAC/Malariacontrol-ODAGrants-KEN",
            name="Malaria control [Grants to Kenya]",
            description="ODA grants to Kenya for malaria control.",
            population_type=["DevelopmentFinance"],
            measured_property=["DevelopmentFinanceFlow"],
            stat_type=["measuredValue"],
            constraints={
                "DevelopmentFinancePurpose": ["DAC/Malariacontrol"],
                "DevelopmentFinanceRecipient": ["country/KEN"],
                "DevelopmentFinanceScheme": ["ODAGrants"],
            },
        ),
        StatVarFeatures(
            dcid="ONE/CRS_DAC/Health-ODAGrants-F",
            name="Health [Grants to Africa]",
            description="ODA grants to Africa (DAC code F) for health.",
            population_type=["DevelopmentFinance"],
            measured_property=["DevelopmentFinanceFlow"],
            stat_type=["measuredValue"],
            constraints={
                "DevelopmentFinancePurpose": ["DAC/Health"],
                "DevelopmentFinanceRecipient": ["DAC/Africa"],
                "DevelopmentFinanceScheme": ["ODAGrants"],
            },
        ),
    ]


@pytest.fixture
def census_candidates():
    from dc_search.retrieval import StatVarFeatures

    return [
        StatVarFeatures(
            dcid="Count_Person",
            name="Total Population",
            description="Total count of persons.",
            population_type=["Person"],
            measured_property=["count"],
            stat_type=["measuredValue"],
        ),
        StatVarFeatures(
            dcid="Count_Person_Female",
            name="Female Population",
            description="Count of female persons.",
            population_type=["Person"],
            measured_property=["count"],
            stat_type=["measuredValue"],
            constraints={"gender": ["Female"]},
        ),
        StatVarFeatures(
            dcid="Count_MortalityEvent_Cause-HIVAIDS",
            name="HIV/AIDS deaths",
            description="Count of mortality events caused by HIV/AIDS.",
            population_type=["MortalityEvent"],
            measured_property=["count"],
            stat_type=["measuredValue"],
            constraints={"causeOfDeath": ["HIVAIDS"]},
        ),
        StatVarFeatures(
            dcid="Count_MedicalConditionIncident_ConditionHIVAIDS",
            name="HIV/AIDS cases",
            description="Count of HIV/AIDS medical incidents.",
            population_type=["MedicalConditionIncident"],
            measured_property=["count"],
            stat_type=["measuredValue"],
            constraints={"medicalCondition": ["HIVAIDS"]},
        ),
    ]


@pytest.fixture
def sdg_candidates():
    from dc_search.retrieval import StatVarFeatures

    return [
        StatVarFeatures(
            dcid="sdg/SH_STA_MORT",
            name="Maternal mortality ratio",
            description="SDG 3.1.1 maternal mortality ratio.",
            population_type=["SDG_SH_STA_MORT"],
            measured_property=["value"],
            stat_type=["measuredValue"],
        ),
        StatVarFeatures(
            dcid="sdg/SG_GEN_PARL",
            name="Women in national parliaments",
            description="SDG 5.5.1 proportion of seats held by women.",
            population_type=["SDG_SG_GEN_PARL"],
            measured_property=["value"],
            stat_type=["measuredValue"],
        ),
    ]


@pytest.fixture
def who_candidates():
    from dc_search.retrieval import StatVarFeatures

    return [
        StatVarFeatures(
            dcid="ONE/who_dis13",
            name="Malaria incidence",
            description="WHO indicator dis13 (malaria).",
            population_type=["Thing"],
            measured_property=["who/dis13"],
            stat_type=["measuredValue"],
        ),
        StatVarFeatures(
            dcid="ONE/who_CM_03",
            name="Neonatal mortality rate (WHO/CM_03)",
            description="WHO causes of mortality indicator CM_03.",
            population_type=["Person"],
            measured_property=["who/CM_03"],
            stat_type=["measuredValue"],
        ),
    ]


@pytest.fixture
def mixed_candidates(
    crs_dac_candidates,
    census_candidates,
    sdg_candidates,
    who_candidates,
):
    return [
        *crs_dac_candidates,
        *census_candidates,
        *sdg_candidates,
        *who_candidates,
    ]
