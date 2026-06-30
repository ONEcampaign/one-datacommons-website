"""Tests for S3 observability fields: llm_usage, n_recalled, data_date_range,
data_confirmed_at_recipient, and nearest_real.

Also pins spec_id for two known goldens to prove new StatVar/Diagnostics fields
do not affect the hash.
"""
from __future__ import annotations

from qre.engine.assemble import build_stat_vars, build_spec, assemble_no_data, make_diagnostics
from qre.engine.conjoin import assemble_region
from qre.engine.graph import Facet
from qre.engine.families import DEV_FINANCE_FAMILY
from qre.engine.shape import build_shape
from qre.engine.assemble import build_shape_model, build_slot, now_ms
from qre.engine.bind import SlotBindingDraft
from qre.engine.regions import RegionResult
from qre.models import (
    CoverageBare,
    DateRange,
    DefiniteResponse,
    Diagnostics,
    Entity,
    EntityRoleSubject,
    GraphRef,
    NoDataResponse,
    PipelineStep,
    QueryEcho,
    RawTextInput,
    ResolveRequest,
    StatVar,
    Timing,
)
from tests.engine._harness import offline_resolve


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _query_echo() -> QueryEcho:
    return QueryEcho(
        entry_path="raw_text",
        raw_query="test",
        normalized_query="test",
        variable_text=["test"],
        extract_skipped=False,
    )


def _diagnostics(**kw) -> Diagnostics:
    return Diagnostics(engine_build="test", warnings=[], timing_ms=Timing(total=0), **kw)


def _sv_ref(dcid: str) -> GraphRef:
    return GraphRef(dcid=dcid, label=dcid)


def _facet(earliest: str | None, latest: str | None) -> Facet:
    return Facet(earliest_date=earliest, latest_date=latest, obs_count=10)


# ---------------------------------------------------------------------------
# F9: llm_usage in Diagnostics
# ---------------------------------------------------------------------------

class TestLLMUsage:
    def test_passes_through_explicit_usage(self):
        """make_diagnostics carries an explicit llm_usage into Diagnostics."""
        usage = {"input_tokens": 100, "output_tokens": 50, "cached_tokens": 10}
        d = make_diagnostics("build", [], {}, 10, llm_usage=usage)
        assert d.llm_usage == usage

    def test_none_when_usage_omitted(self):
        """The no-LLM early-return paths omit llm_usage and get None."""
        d = make_diagnostics("build", [], {}, 10)
        assert d.llm_usage is None


# ---------------------------------------------------------------------------
# F13: n_recalled in ResolutionTrace
# ---------------------------------------------------------------------------

class TestNRecalled:
    def _minimal_spec(self, n_recalled: int | None):
        family = DEV_FINANCE_FAMILY
        shape_draft = build_shape(family)
        five_tuple_refs = {
            "DevelopmentFinance": GraphRef(dcid="DevelopmentFinance", label="Dev Finance"),
            "DevelopmentFinanceFlow": GraphRef(dcid="DevelopmentFinanceFlow", label="Flow"),
            "measuredValue": GraphRef(dcid="measuredValue", label="Measured Value"),
        }
        scheme_draft = SlotBindingDraft(
            axis="what", property_dcid="DevelopmentFinanceScheme", kind="value", value_dcids=["ODAGrants"]
        )
        prop_ref = GraphRef(dcid="DevelopmentFinanceScheme", label="DevelopmentFinanceScheme")
        slot = build_slot(shape_draft.slot_keys[0], scheme_draft, [GraphRef(dcid="ODAGrants", label="ODA Grants")], property_ref=prop_ref)
        shape_model = build_shape_model(shape_draft, [slot.key], five_tuple_refs, member_count=1)
        sv_ref = GraphRef(dcid="TEST_SV", label="Test SV")
        stat_vars = [StatVar(ref=sv_ref, shape_id=shape_draft.shape_id, slot_values=[])]
        entity = Entity(
            ref=GraphRef(dcid="country/ETH", label="Ethiopia"),
            entity_type=None,
            role=EntityRoleSubject(),
        )
        return build_spec(
            shape=shape_model,
            slots=[slot],
            stat_vars=stat_vars,
            entities=[entity],
            coverage=CoverageBare(has_data=True),
            pipeline_trace=[PipelineStep(step="extract", ran=True)],
            n_recalled=n_recalled,
        )

    def test_n_recalled_propagates_to_resolution_trace(self):
        spec = self._minimal_spec(n_recalled=7)
        assert spec.resolution.n_recalled == 7

    def test_n_recalled_none_by_default(self):
        spec = self._minimal_spec(n_recalled=None)
        assert spec.resolution.n_recalled is None

    def test_n_recalled_populated_in_e2e_golden(self):
        """End-to-end: health ODA grants golden yields n_recalled as int > 0."""
        result = offline_resolve(
            ResolveRequest(input=RawTextInput(query="health ODA grants from USA to Ethiopia"))
        )
        assert isinstance(result.root, DefiniteResponse)
        spec = result.root.interpretation
        assert isinstance(spec.resolution.n_recalled, int)
        assert spec.resolution.n_recalled > 0


# ---------------------------------------------------------------------------
# F10: data_date_range and data_confirmed_at_recipient in StatVar
# ---------------------------------------------------------------------------

class TestDataDateRangeAndRecipient:
    def test_date_range_from_facets(self):
        """build_stat_vars derives start=min(earliest) and end=max(latest) from facets."""
        sv_ref = _sv_ref("SV_A")
        facets = [
            _facet("2010", "2015"),
            _facet("2012", "2020"),
        ]
        stat_vars = build_stat_vars(
            sv_refs=[sv_ref],
            shape_id="shape1",
            slots=[],
            facets_by_sv={"SV_A": facets},
        )
        assert len(stat_vars) == 1
        dr = stat_vars[0].data_date_range
        assert dr is not None
        assert dr.start == "2010"
        assert dr.end == "2020"

    def test_date_range_none_when_no_facets_provided(self):
        """When facets_by_sv is None, data_date_range stays None."""
        sv_ref = _sv_ref("SV_A")
        stat_vars = build_stat_vars(sv_refs=[sv_ref], shape_id="s", slots=[])
        assert stat_vars[0].data_date_range is None

    def test_date_range_none_when_sv_not_in_map(self):
        """A SV absent from facets_by_sv gets date_range=None."""
        sv_ref = _sv_ref("SV_ABSENT")
        stat_vars = build_stat_vars(
            sv_refs=[sv_ref],
            shape_id="s",
            slots=[],
            facets_by_sv={"SV_OTHER": [_facet("2010", "2020")]},
        )
        assert stat_vars[0].data_date_range is None

    def test_recipient_confirmed_true(self):
        """SV in recipient_confirmed set → data_confirmed_at_recipient=True."""
        sv_ref = _sv_ref("SV_A")
        stat_vars = build_stat_vars(
            sv_refs=[sv_ref],
            shape_id="s",
            slots=[],
            recipient_confirmed={"SV_A", "SV_B"},
        )
        assert stat_vars[0].data_confirmed_at_recipient is True

    def test_recipient_confirmed_false_for_donor_only(self):
        """SV absent from recipient_confirmed set → data_confirmed_at_recipient=False."""
        sv_ref = _sv_ref("SV_DONOR_ONLY")
        stat_vars = build_stat_vars(
            sv_refs=[sv_ref],
            shape_id="s",
            slots=[],
            recipient_confirmed={"SV_DIFFERENT"},
        )
        assert stat_vars[0].data_confirmed_at_recipient is False

    def test_recipient_confirmed_none_when_not_checked(self):
        """When recipient_confirmed is None, data_confirmed_at_recipient stays None."""
        sv_ref = _sv_ref("SV_A")
        stat_vars = build_stat_vars(sv_refs=[sv_ref], shape_id="s", slots=[])
        assert stat_vars[0].data_confirmed_at_recipient is None

    def test_date_range_partial_dates(self):
        """If only earliest_date is set, end is None (and vice versa)."""
        sv_ref = _sv_ref("SV_A")
        facets = [_facet("2015", None)]
        stat_vars = build_stat_vars(
            sv_refs=[sv_ref],
            shape_id="s",
            slots=[],
            facets_by_sv={"SV_A": facets},
        )
        dr = stat_vars[0].data_date_range
        assert dr is not None
        assert dr.start == "2015"
        assert dr.end is None


# ---------------------------------------------------------------------------
# F18: nearest_real in NoData
# ---------------------------------------------------------------------------

class TestNearestReal:
    def test_nearest_real_passed_through_assemble_no_data(self):
        """assemble_no_data threads nearest_real into NoData.nearest_real."""
        # Build a minimal Spec to use as nearest_real
        from tests.engine.test_observability import TestNRecalled
        spec = TestNRecalled()._minimal_spec(n_recalled=None)
        response = assemble_no_data(
            reason="no_observations",
            query_echo=_query_echo(),
            diagnostics=_diagnostics(),
            nearest_real=[spec],
        )
        inner = response.root
        assert isinstance(inner, NoDataResponse)
        assert inner.no_data.nearest_real is not None
        assert len(inner.no_data.nearest_real) == 1
        assert inner.no_data.nearest_real[0].spec_id == spec.spec_id

    def test_nearest_real_none_when_nothing_relaxes(self):
        """assemble_no_data with nearest_real=None leaves NoData.nearest_real as None."""
        response = assemble_no_data(
            reason="no_observations",
            query_echo=_query_echo(),
            diagnostics=_diagnostics(),
            nearest_real=None,
        )
        inner = response.root
        assert isinstance(inner, NoDataResponse)
        assert inner.no_data.nearest_real is None

    def test_nearest_real_preserved_through_assemble_region(self):
        """CR-1: assemble_region must pass nearest_real to assemble_no_data.

        The previous bug: assemble_region called assemble_no_data without nearest_real,
        so region.nearest_real was silently dropped. This test would have caught it.
        """
        from tests.engine.test_observability import TestNRecalled
        spec = TestNRecalled()._minimal_spec(n_recalled=None)
        region = RegionResult(
            variable_text="test",
            status="no_data",
            specs=(),
            no_data_reason="no_observations",
            warnings=(),
            timing_by_step={},
            nearest_real=(spec,),
        )
        response = assemble_region(
            region,
            query="test",
            variable_texts=["test"],
            extra_warnings=[],
            start_ms=now_ms(),
            engine_build="test-build",
            include_sentence=False,
            max_candidates=6,
        )
        inner = response.root
        assert isinstance(inner, NoDataResponse)
        assert inner.no_data.nearest_real is not None
        assert len(inner.no_data.nearest_real) == 1
        assert inner.no_data.nearest_real[0].spec_id == spec.spec_id


# ---------------------------------------------------------------------------
# CR-2 end-to-end: llm_usage populated via resolve_async
# ---------------------------------------------------------------------------

class TestLLMUsageEndToEnd:
    def test_llm_usage_non_none_after_resolve(self):
        """CR-2: llm_usage must be non-None in diagnostics after a full resolve_async call.

        FakeLLM returns zeros usage; the explicit return-and-aggregate path (not the
        ContextVar) must thread it through so diagnostics.llm_usage arrives populated.
        """
        result = offline_resolve(
            ResolveRequest(input=RawTextInput(query="health ODA grants from USA to Ethiopia"))
        )
        diag = result.root.diagnostics  # type: ignore[attr-defined]
        assert diag.llm_usage is not None, (
            "llm_usage was None; the extract/bind usage was not threaded through"
        )
        assert set(diag.llm_usage.keys()) == {"input_tokens", "output_tokens", "cached_tokens"}

    def test_llm_usage_non_none_standard_golden(self):
        """CR-2: standard-DC path (no bind call) must still report llm_usage from extract."""
        result = offline_resolve(
            ResolveRequest(input=RawTextInput(query="total population India"))
        )
        diag = result.root.diagnostics  # type: ignore[attr-defined]
        assert diag.llm_usage is not None
        assert set(diag.llm_usage.keys()) == {"input_tokens", "output_tokens", "cached_tokens"}


# ---------------------------------------------------------------------------
# spec_id stability: new fields must not affect the hash
# ---------------------------------------------------------------------------

class TestSpecIdUnchanged:
    def test_health_oda_grants_spec_id_pinned(self):
        """Definite golden: health ODA grants — spec_id must not drift."""
        result = offline_resolve(
            ResolveRequest(input=RawTextInput(query="health ODA grants from USA to Ethiopia"))
        )
        assert isinstance(result.root, DefiniteResponse)
        spec = result.root.interpretation
        assert spec.spec_id == "spec_4da03f8add738268"

    def test_total_population_india_spec_id_pinned(self):
        """Definite golden: total population India — spec_id must not drift."""
        result = offline_resolve(
            ResolveRequest(input=RawTextInput(query="total population India"))
        )
        assert isinstance(result.root, DefiniteResponse)
        spec = result.root.interpretation
        assert spec.spec_id == "spec_88368e10737ab605"


# ---------------------------------------------------------------------------
# R1: StatVar observability on the standard + dev-finance construct paths
#
# The bug: data_date_range / data_confirmed_at_recipient were derived only from the
# graph_confirm_resolve fallback's Materialised. The STANDARD materialise and the
# dev-finance CONSTRUCT path built Materialised WITHOUT facets_by_sv, so these fields
# came through as None even though observations were read. These end-to-end tests run
# the two common queries through resolve_async (FakeGraph carrying dated observations)
# and assert the fields arrive populated.
# ---------------------------------------------------------------------------

class TestStatVarObservabilityNonFallbackPaths:
    def test_standard_path_populates_statvar_observability(self):
        """Standard path ('total population India') derives date range + recipient flag."""
        result = offline_resolve(
            ResolveRequest(input=RawTextInput(query="total population India"))
        )
        assert isinstance(result.root, DefiniteResponse)
        stat_vars = result.root.interpretation.stat_vars
        assert stat_vars, "expected at least one resolved StatVar"
        sv = stat_vars[0]
        assert sv.data_date_range is not None, (
            "standard path read observations but data_date_range came through None"
        )
        assert sv.data_date_range.start is not None
        # Probe entity IS the subject entity (read directly) → confirmed at recipient.
        assert sv.data_confirmed_at_recipient is True

    def test_dev_finance_construct_populates_statvar_observability(self):
        """Dev-finance CONSTRUCT path ('health ODA grants USA→Ethiopia') derives the fields."""
        result = offline_resolve(
            ResolveRequest(
                input=RawTextInput(query="health ODA grants from USA to Ethiopia")
            )
        )
        assert isinstance(result.root, DefiniteResponse)
        stat_vars = result.root.interpretation.stat_vars
        assert stat_vars, "expected at least one resolved StatVar"
        sv = stat_vars[0]
        assert sv.data_date_range is not None, (
            "construct path read observations but data_date_range came through None"
        )
        assert sv.data_date_range.start is not None
        # Construct probe reads the donor's reported figures, not the recipient
        # entity directly, so the flag is set-but-False (not None).
        assert sv.data_confirmed_at_recipient is False


# ---------------------------------------------------------------------------
# R3: a failing nearest_real relaxation probe must not fail the no_data response.
#
# The OPTIONAL _suggest_nearest_real relaxation probe calls graph_confirm_resolve,
# which issues observation_facets_batch. If that raises GraphInfraError, an
# otherwise-valid no_data response must NOT become a 503 — the probe's contract is
# "returns an empty list on any error", so nearest_real stays None/empty and the
# 200 no_data response goes through.
# ---------------------------------------------------------------------------

class TestNearestRealProbeFailureIsContained:
    def test_probe_graph_error_still_yields_200_no_data(self):
        import asyncio  # noqa: PLC0415
        from datetime import date  # noqa: PLC0415
        from unittest.mock import patch  # noqa: PLC0415

        from qre.engine.core import resolve_async  # noqa: PLC0415
        from qre.engine.errors import GraphInfraError  # noqa: PLC0415
        from tests.engine._harness import PINNED_DATE  # noqa: PLC0415
        from tests.fixtures import FakeGraph, FakeLLM  # noqa: PLC0415

        class _BatchRaisingGraph(FakeGraph):
            """Delegates everything to fixtures but blows up on the batch obs probe.

            observation_facets_batch is used ONLY by graph_confirm_resolve, which the
            relaxation probe drives. The primary dev-finance construct path uses
            observation_facets (singular) + node_label, so the no_observations
            decision is reached normally; only the optional probe hits the error.
            """

            def observation_facets_batch(self, *_args, **_kwargs):
                raise GraphInfraError("simulated 503 in relaxation probe", upstream_status=503)

        request = ResolveRequest(
            input=RawTextInput(query="health ODA grants from USA to Nauru")
        )
        with patch("qre.engine.extract.date") as mock_date:
            mock_date.today.return_value = PINNED_DATE
            mock_date.side_effect = lambda *a, **k: date(*a, **k)
            result = asyncio.run(
                resolve_async(request, graph=_BatchRaisingGraph(), llm=FakeLLM())
            )

        inner = result.root
        assert isinstance(inner, NoDataResponse), (
            "probe GraphInfraError leaked and changed the response type (expected no_data)"
        )
        assert inner.no_data.reason == "no_observations"
        # The failed probe must yield no suggestions, not a 503.
        assert not inner.no_data.nearest_real
