"""Path C tests: spec_resubmit named-family refine + standard promote.

All tests are offline (FakeGraph, no LLM), exercising resolve_spec_resubmit
directly and also end-to-end via offline_resolve.

Fixture notes (for H1 coordinator):
  - Dev-finance test uses ONE/CRS_DAC/Health-ODAGrants-ETH which IS in
    graph_nodes.json, and the observation ONE/CRS_DAC/Health-ODAGrants-ETH|country/USA
    which IS in graph_obs.json.
  - Standard promote test uses Amount_EconomicActivity_GrossDomesticProduction_Nominal
    (in graph_nodes.json) with entity country/IND
    (observation key in graph_obs.json).
  - Both fixture sets already exist; no new fixture capture is needed for these
    specific tests.  Additional standard SVs or entity DCIDs may require H1 capture.
"""
from __future__ import annotations

import pytest

from qre.engine.errors import EngineInputError
from qre.engine.families import rule_for_shape_id
from qre.engine.families.dev_finance import DEV_FINANCE_RULE
from qre.engine.regions import resolve_spec_resubmit
from qre.models import (
    Axis,
    BindingUnbound,
    BindingValue,
    GraphRef,
    ResolveOptions,
    ResolveRequest,
    Slot,
    SlotKey,
    SlotValue,
    SpecResubmitInput,
)
from tests.engine._harness import offline_resolve
from tests.fixtures import FakeGraph

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _gref(dcid: str, label: str) -> GraphRef:
    return GraphRef(dcid=dcid, label=label)


def _value_slot(
    axis: Axis,
    prop_dcid: str,
    prop_label: str,
    val_dcid: str,
    val_label: str,
) -> Slot:
    """Build a Slot with a BindingValue and a graph-ref value."""
    return Slot(
        key=SlotKey(
            axis=axis,
            property=_gref(prop_dcid, prop_label),
            label=prop_label,
        ),
        binding=BindingValue(
            value=SlotValue(
                ref=_gref(val_dcid, val_label),
                value_kind="enum_value",
            )
        ),
    )


def _unbound_slot(axis: Axis, prop_dcid: str, prop_label: str) -> Slot:
    return Slot(
        key=SlotKey(
            axis=axis,
            property=_gref(prop_dcid, prop_label),
            label=prop_label,
        ),
        binding=BindingUnbound(),
    )


# Standard dev-finance slots (Health ODA Grants to Ethiopia)
_SCHEME_SLOT = _value_slot(
    "what", "DevelopmentFinanceScheme", "finance scheme",
    "ODAGrants", "Official Development Assistance Grants",
)
_PURPOSE_SLOT = _value_slot(
    "how", "DevelopmentFinancePurpose", "sector/purpose",
    "DAC/Health", "Health (Total)",
)


# ---------------------------------------------------------------------------
# SpecResubmitInput contract: new optional fields
# ---------------------------------------------------------------------------


class TestSpecResubmitInputFields:
    def test_stat_var_dcids_defaults_none(self):
        inp = SpecResubmitInput(shape_id="some_shape", slots=[])
        assert inp.stat_var_dcids is None

    def test_entity_dcids_defaults_none(self):
        inp = SpecResubmitInput(shape_id="some_shape", slots=[])
        assert inp.entity_dcids is None

    def test_stat_var_dcids_round_trips(self):
        inp = SpecResubmitInput(
            shape_id="dev_finance_crs_dac",
            slots=[],
            stat_var_dcids=["SomeVar1", "SomeVar2"],
        )
        assert inp.stat_var_dcids == ["SomeVar1", "SomeVar2"]

    def test_entity_dcids_round_trips(self):
        inp = SpecResubmitInput(
            shape_id="some_shape",
            slots=[],
            entity_dcids=["country/ETH"],
        )
        assert inp.entity_dcids == ["country/ETH"]

    def test_stat_var_dcids_max_length_101_raises(self):
        """Over 100 stat_var_dcids must be rejected at validation."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SpecResubmitInput(
                shape_id="s",
                slots=[],
                stat_var_dcids=["sv"] * 101,
            )

    def test_entity_dcids_max_length_51_raises(self):
        """Over 50 entity_dcids must be rejected at validation."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            SpecResubmitInput(
                shape_id="s",
                slots=[],
                entity_dcids=["e"] * 51,
            )


# ---------------------------------------------------------------------------
# max_candidates ge=2 validation
# ---------------------------------------------------------------------------


class TestMaxCandidatesValidation:
    def test_max_candidates_1_raises_validation_error(self):
        """max_candidates=1 must be rejected at input validation."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ResolveOptions(max_candidates=1)

    def test_max_candidates_0_raises_validation_error(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ResolveOptions(max_candidates=0)

    def test_max_candidates_2_is_valid(self):
        opts = ResolveOptions(max_candidates=2)
        assert opts.max_candidates == 2

    def test_max_candidates_clamped_via_resolve(self):
        """max_candidates supplied > server ceiling is clamped; result is definite."""
        from qre.engine.config import QRE_MAX_CANDIDATES
        request = ResolveRequest(
            input=SpecResubmitInput(
                shape_id="dev_finance_crs_dac",
                slots=[_SCHEME_SLOT, _PURPOSE_SLOT],
                entity_dcids=["country/ETH"],
            ),
            options=ResolveOptions(max_candidates=QRE_MAX_CANDIDATES + 100),
        )
        result = offline_resolve(request)
        # Should be definite (one SV found), not an error
        assert result.root.status == "definite"


# ---------------------------------------------------------------------------
# dev_finance named-family refine path
# ---------------------------------------------------------------------------


class TestDevFinanceRefine:
    """Named-family path: resolve_spec_resubmit with rule=DEV_FINANCE_RULE."""

    def _make_inp(self, slots=None, entity_dcids=None):
        return SpecResubmitInput(
            shape_id="dev_finance_crs_dac",
            slots=slots or [_SCHEME_SLOT, _PURPOSE_SLOT],
            entity_dcids=entity_dcids or ["country/ETH"],
        )

    def test_definite_result(self):
        """Dev-finance resubmit with valid slots → definite RegionResult."""
        inp = self._make_inp()
        rule = rule_for_shape_id(shape_id="dev_finance_crs_dac")
        assert rule is DEV_FINANCE_RULE
        region = resolve_spec_resubmit(inp=inp, rule=rule, graph=FakeGraph())
        assert region.status == "definite"
        assert len(region.specs) == 1

    def test_extract_skipped_in_trace(self):
        """The extract step must be in the pipeline trace with ran=False."""
        inp = self._make_inp()
        rule = rule_for_shape_id(shape_id="dev_finance_crs_dac")
        region = resolve_spec_resubmit(inp=inp, rule=rule, graph=FakeGraph())
        spec = region.specs[0]
        trace = spec.resolution.pipeline_trace
        extract_steps = [s for s in trace if s.step == "extract"]
        assert extract_steps, "Expected an 'extract' step in pipeline_trace"
        assert extract_steps[0].ran is False

    def test_entry_path_via_full_resolve(self):
        """End-to-end: spec_resubmit sets entry_path=spec_resubmit + extract_skipped."""
        request = ResolveRequest(
            input=SpecResubmitInput(
                shape_id="dev_finance_crs_dac",
                slots=[_SCHEME_SLOT, _PURPOSE_SLOT],
                entity_dcids=["country/ETH"],
            )
        )
        result = offline_resolve(request)
        assert result.root.status == "definite"
        echo = result.root.query_echo
        assert echo.entry_path == "spec_resubmit"
        assert echo.extract_skipped is True

    def test_graph_read_labels_not_posted_labels(self):
        """Labels in the spec come from the graph, not from posted slot labels (decision #2)."""
        # Post stale/wrong labels; the graph fixture has the correct ones.
        stale_scheme_slot = _value_slot(
            "what", "DevelopmentFinanceScheme", "STALE SCHEME LABEL",
            "ODAGrants", "STALE VALUE LABEL",
        )
        stale_purpose_slot = _value_slot(
            "how", "DevelopmentFinancePurpose", "STALE PURPOSE LABEL",
            "DAC/Health", "STALE HEALTH LABEL",
        )
        inp = SpecResubmitInput(
            shape_id="dev_finance_crs_dac",
            slots=[stale_scheme_slot, stale_purpose_slot],
            entity_dcids=["country/ETH"],
        )
        rule = rule_for_shape_id(shape_id="dev_finance_crs_dac")
        region = resolve_spec_resubmit(inp=inp, rule=rule, graph=FakeGraph())
        assert region.status == "definite"
        # The spec's stat_var should carry the graph-read label, not the stale posted one.
        spec = region.specs[0]
        sv = spec.stat_vars[0]
        assert sv.ref.label != "STALE VALUE LABEL"

    def test_absent_posted_dcid_yields_no_data(self):
        """A posted value dcid absent from the graph → no_data (decision #2)."""
        bad_slot = _value_slot(
            "what", "DevelopmentFinanceScheme", "finance scheme",
            "NONEXISTENT_DCID_XYZ", "some label",
        )
        inp = SpecResubmitInput(
            shape_id="dev_finance_crs_dac",
            slots=[bad_slot, _PURPOSE_SLOT],
            entity_dcids=["country/ETH"],
        )
        rule = rule_for_shape_id(shape_id="dev_finance_crs_dac")
        region = resolve_spec_resubmit(inp=inp, rule=rule, graph=FakeGraph())
        assert region.status == "no_data"

    def test_variable_text_set_to_shape_id(self):
        """variable_text in the region echoes the shape_id (best label without extraction)."""
        inp = self._make_inp()
        rule = rule_for_shape_id(shape_id="dev_finance_crs_dac")
        region = resolve_spec_resubmit(inp=inp, rule=rule, graph=FakeGraph())
        assert region.variable_text == "dev_finance_crs_dac"


# ---------------------------------------------------------------------------
# standard promote path
# ---------------------------------------------------------------------------


_GDP_SV_DCID = "Amount_EconomicActivity_GrossDomesticProduction_Nominal"
_GDP_SHAPE_ID = "economicactivity_amount_measuredvalue_nominal"
_ENTITY_DCID = "country/IND"

# The activitySource constraint on this SV: GrossDomesticProduction
_ACTIVITY_SOURCE_SLOT = _value_slot(
    "how", "activitySource", "activity source",
    "GrossDomesticProduction", "Gross Domestic Production",
)


class TestStandardPromote:
    """Standard promote path: resolve_spec_resubmit with rule=None."""

    def _make_inp(self, stat_var_dcids=None, entity_dcids=None, slots=None):
        return SpecResubmitInput(
            shape_id=_GDP_SHAPE_ID,
            slots=slots if slots is not None else [_ACTIVITY_SOURCE_SLOT],
            stat_var_dcids=stat_var_dcids or [_GDP_SV_DCID],
            entity_dcids=entity_dcids or [_ENTITY_DCID],
        )

    def test_definite_from_re_read_anchors(self):
        """Standard promote with valid SV + entity → definite."""
        inp = self._make_inp()
        region = resolve_spec_resubmit(inp=inp, rule=None, graph=FakeGraph())
        assert region.status == "definite"

    def test_shape_anchors_from_graph_not_shape_id(self):
        """Five-tuple anchors come from re-reading SV arcs, not from parsing shape_id."""
        inp = self._make_inp()
        region = resolve_spec_resubmit(inp=inp, rule=None, graph=FakeGraph())
        spec = region.specs[0]
        # population_type is the re-read five-tuple anchor
        assert spec.shape.population_type.dcid == "EconomicActivity"
        assert spec.shape.measured_property.dcid == "amount"

    def test_extract_skipped_trace(self):
        """Standard promote must record extract step with ran=False."""
        inp = self._make_inp()
        region = resolve_spec_resubmit(inp=inp, rule=None, graph=FakeGraph())
        spec = region.specs[0]
        extract_steps = [s for s in spec.resolution.pipeline_trace if s.step == "extract"]
        assert extract_steps and extract_steps[0].ran is False

    def test_missing_stat_var_dcids_raises(self):
        """Standard shape_id with no stat_var_dcids → EngineInputError (no code)."""
        inp = SpecResubmitInput(
            shape_id=_GDP_SHAPE_ID,
            slots=[],
            stat_var_dcids=None,
            entity_dcids=[_ENTITY_DCID],
        )
        with pytest.raises(EngineInputError) as exc_info:
            resolve_spec_resubmit(inp=inp, rule=None, graph=FakeGraph())
        assert exc_info.value.code is None

    def test_unknown_shape_id_raises(self):
        """An unknown shape_id with no stat_var_dcids → EngineInputError (no code)."""
        inp = SpecResubmitInput(
            shape_id="completely_unknown_shape_xyz",
            slots=[],
            stat_var_dcids=None,
        )
        # rule_for_shape_id returns None → standard path → raises on missing stat_var_dcids
        with pytest.raises(EngineInputError) as exc_info:
            resolve_spec_resubmit(inp=inp, rule=None, graph=FakeGraph())
        assert exc_info.value.code is None

    def test_absent_posted_sv_yields_no_data(self):
        """A posted SV dcid absent from the graph → no_data."""
        inp = SpecResubmitInput(
            shape_id=_GDP_SHAPE_ID,
            slots=[],
            stat_var_dcids=["NONEXISTENT_SV_12345"],
            entity_dcids=[_ENTITY_DCID],
        )
        region = resolve_spec_resubmit(inp=inp, rule=None, graph=FakeGraph())
        assert region.status == "no_data"

    def test_shape_id_mismatch_raises(self):
        """SV posted under a mismatched shape_id → EngineInputError (no code)."""
        inp = SpecResubmitInput(
            shape_id="completely_wrong_shape_id",
            slots=[],
            stat_var_dcids=[_GDP_SV_DCID],
            entity_dcids=[_ENTITY_DCID],
        )
        with pytest.raises(EngineInputError) as exc_info:
            resolve_spec_resubmit(inp=inp, rule=None, graph=FakeGraph())
        assert exc_info.value.code is None

    def test_edited_binding_raises_promote_only(self):
        """Posting a slot value NOT in the SV's constraints → EngineInputError code=promote_only."""
        # activitySource on the GDP SV has value GrossDomesticProduction; post a different one.
        edited_slot = _value_slot(
            "how", "activitySource", "activity source",
            "SomeOtherValue_NotInConstraints", "some label",
        )
        inp = SpecResubmitInput(
            shape_id=_GDP_SHAPE_ID,
            slots=[edited_slot],
            stat_var_dcids=[_GDP_SV_DCID],
            entity_dcids=[_ENTITY_DCID],
        )
        with pytest.raises(EngineInputError) as exc_info:
            resolve_spec_resubmit(inp=inp, rule=None, graph=FakeGraph())
        assert exc_info.value.code == "promote_only"

    def test_where_slot_entity_not_refine_out_flagged(self):
        """A where-axis slot entity is never checked by the REFINE-out guard."""
        # Post a where slot with a geographic entity; this must NOT trigger promote_only.
        where_slot = _value_slot(
            "where", "someProperty", "some place prop",
            "country/BRA", "Brazil",
        )
        inp = SpecResubmitInput(
            shape_id=_GDP_SHAPE_ID,
            slots=[where_slot],
            stat_var_dcids=[_GDP_SV_DCID],
            entity_dcids=[_ENTITY_DCID],
        )
        # Should not raise; where slots skip the REFINE-out guard.
        region = resolve_spec_resubmit(inp=inp, rule=None, graph=FakeGraph())
        # Result may be no_data (someProperty not a real constraint) but must not raise.
        assert region.status in ("definite", "no_data")

    def test_entity_dcids_precedence_over_where_slot(self):
        """entity_dcids[0] takes precedence over a where-slot entity."""
        # Post a where slot with BRA, but entity_dcids=[IND]; IND has observations.
        where_slot = Slot(
            key=SlotKey(axis="where", property=None, label="place"),
            binding=BindingValue(
                value=SlotValue(ref=_gref("country/BRA", "Brazil"), value_kind="entity")
            ),
        )
        inp = SpecResubmitInput(
            shape_id=_GDP_SHAPE_ID,
            slots=[where_slot],
            stat_var_dcids=[_GDP_SV_DCID],
            entity_dcids=[_ENTITY_DCID],  # IND wins
        )
        region = resolve_spec_resubmit(inp=inp, rule=None, graph=FakeGraph())
        # IND has observations in the fixture; BRA also does — but entity_dcids[0] wins.
        # We just verify the call succeeds and produces a definite.
        assert region.status == "definite"

    def test_no_entity_degrades_to_no_data(self):
        """Standard promote without entity and no where-slot → no_data (no 400)."""
        inp = SpecResubmitInput(
            shape_id=_GDP_SHAPE_ID,
            slots=[],
            stat_var_dcids=[_GDP_SV_DCID],
            entity_dcids=None,  # omitted
        )
        region = resolve_spec_resubmit(inp=inp, rule=None, graph=FakeGraph())
        # Without an entity the standard resolver returns bare coverage (no entity probe).
        # CoverageBare.has_data=True counts as definite only for the bare path.
        # Either definite (bare) or no_data is acceptable; must NOT be an error.
        assert region.status in ("definite", "no_data")

    def test_end_to_end_via_offline_resolve(self):
        """Full pipeline path via offline_resolve: spec_resubmit → standard promote."""
        request = ResolveRequest(
            input=SpecResubmitInput(
                shape_id=_GDP_SHAPE_ID,
                slots=[_ACTIVITY_SOURCE_SLOT],
                stat_var_dcids=[_GDP_SV_DCID],
                entity_dcids=[_ENTITY_DCID],
            )
        )
        result = offline_resolve(request)
        assert result.root.status == "definite"
        echo = result.root.query_echo
        assert echo.entry_path == "spec_resubmit"
        assert echo.extract_skipped is True


# ---------------------------------------------------------------------------
# refine_supported on Shape
# ---------------------------------------------------------------------------


class TestRefineSupported:
    def test_dev_finance_shape_refine_supported_true(self):
        """Named-family shapes must have refine_supported=True."""
        inp = SpecResubmitInput(
            shape_id="dev_finance_crs_dac",
            slots=[_SCHEME_SLOT, _PURPOSE_SLOT],
            entity_dcids=["country/ETH"],
        )
        rule = rule_for_shape_id(shape_id="dev_finance_crs_dac")
        region = resolve_spec_resubmit(inp=inp, rule=rule, graph=FakeGraph())
        assert region.status == "definite"
        assert region.specs[0].shape.refine_supported is True

    def test_standard_shape_refine_supported_false(self):
        """Standard shapes must have refine_supported=False."""
        inp = SpecResubmitInput(
            shape_id=_GDP_SHAPE_ID,
            slots=[_ACTIVITY_SOURCE_SLOT],
            stat_var_dcids=[_GDP_SV_DCID],
            entity_dcids=[_ENTITY_DCID],
        )
        region = resolve_spec_resubmit(inp=inp, rule=None, graph=FakeGraph())
        assert region.status == "definite"
        assert region.specs[0].shape.refine_supported is False
