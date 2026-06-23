"""Edge-case tests for the QRE contract models."""
import pytest
from pydantic import ValidationError

from qre import (
    BindingSet,
    BreadthDim,
    CandidateSet,
    CoverageBare,
    CoverageBreadth,
    CoverageExact,
    DefiniteResponse,
    EntityRoleDirectional,
    EntityRoleSubject,
    GraphRef,
    SlotKey,
    SlotValue,
    TimeWindow,
)
from tests.conftest import base_response, minimal_spec


class TestAdditionalInterpretations:
    def test_absent_is_none(self):
        # No additional_interpretations key -> field is None
        resp = DefiniteResponse.model_validate(
            base_response(status="definite", interpretation=minimal_spec())
        )
        assert resp.additional_interpretations is None

    def test_empty_list_preserved(self):
        # [] (deferred cross-shape conjunction signal) is distinct from None
        resp = DefiniteResponse.model_validate(
            base_response(
                status="definite",
                interpretation=minimal_spec(),
                additional_interpretations=[],
            )
        )
        assert resp.additional_interpretations == []
        assert resp.additional_interpretations is not None

    def test_model_dump_preserves_distinction(self):
        resp_none = DefiniteResponse.model_validate(
            base_response(status="definite", interpretation=minimal_spec())
        )
        resp_empty = DefiniteResponse.model_validate(
            base_response(
                status="definite",
                interpretation=minimal_spec(),
                additional_interpretations=[],
            )
        )
        # model_dump preserves []
        assert resp_empty.model_dump()["additional_interpretations"] == []
        # model_dump(exclude_none=True) drops None but keeps []
        assert "additional_interpretations" not in resp_none.model_dump(exclude_none=True)
        assert resp_empty.model_dump(exclude_none=True)["additional_interpretations"] == []


class TestSlotValueKinds:
    def test_time_window_kind(self):
        sv = SlotValue(value_kind="time_window", time_window=TimeWindow(start_year=2015))
        assert sv.value_kind == "time_window"
        assert sv.time_window is not None
        assert sv.time_window.start_year == 2015

    def test_literal_kind(self):
        sv = SlotValue(value_kind="literal", literal="some literal value")
        assert sv.value_kind == "literal"
        assert sv.literal == "some literal value"

    def test_entity_kind_with_ref(self):
        ref = GraphRef(dcid="country/ETH", label="Ethiopia")
        sv = SlotValue(value_kind="entity", ref=ref)
        assert sv.value_kind == "entity"
        assert sv.ref is not None
        assert sv.ref.dcid == "country/ETH"


class TestEntityRole:
    def test_subject_kind_only(self):
        role = EntityRoleSubject(kind="subject")
        assert role.kind == "subject"

    def test_directional_requires_role_and_direction(self):
        role = EntityRoleDirectional(
            kind="directional",
            role=GraphRef(dcid="DevelopmentFinanceRecipient", label="recipient"),
            direction="to",
        )
        assert role.kind == "directional"
        assert role.direction == "to"

    def test_directional_missing_direction_raises(self):
        with pytest.raises(ValidationError):
            EntityRoleDirectional.model_validate(
                {"kind": "directional", "role": {"dcid": "Recipient", "label": "recipient"}}
            )


class TestCoverageArms:
    def test_exact_requires_observation_count(self):
        cov = CoverageExact(kind="exact", has_data=True, observation_count=42)
        assert cov.observation_count == 42

    def test_exact_missing_observation_count_raises(self):
        with pytest.raises(ValidationError):
            CoverageExact.model_validate({"kind": "exact", "has_data": True})

    def test_breadth_requires_non_empty_dimensions(self):
        # empty dimensions should raise (min_length=1)
        with pytest.raises(ValidationError):
            CoverageBreadth.model_validate(
                {"kind": "breadth", "has_data": True, "dimensions": []}
            )

    def test_breadth_with_dimensions_validates(self):
        cov = CoverageBreadth(
            kind="breadth",
            has_data=True,
            dimensions=[BreadthDim(label="donors", count=32)],
        )
        assert len(cov.dimensions) == 1

    def test_bare_requires_only_kind_and_has_data(self):
        cov = CoverageBare(kind="bare", has_data=False)
        assert cov.has_data is False
        assert cov.window is None


class TestSlotKeyPropertyNullable:
    def test_when_axis_no_property(self):
        key = SlotKey(axis="when", label="period")
        assert key.property is None

    def test_how_axis_with_property(self):
        key = SlotKey(
            axis="how",
            property=GraphRef(dcid="DevelopmentFinancePurpose", label="purpose"),
            label="purpose",
        )
        assert key.property is not None
        assert key.property.dcid == "DevelopmentFinancePurpose"


class TestBindingSetMembership:
    def _slot_value(self, dcid: str) -> SlotValue:
        return SlotValue(
            value_kind="enum_value",
            ref=GraphRef(dcid=dcid, label=dcid),
        )

    def test_one_member_set_rejected(self):
        with pytest.raises(ValidationError):
            BindingSet(kind="set", values=[self._slot_value("A")])

    def test_two_member_set_validates(self):
        bs = BindingSet(
            kind="set",
            values=[self._slot_value("A"), self._slot_value("B")],
        )
        assert len(bs.values) == 2


class TestTimeWindow:
    def test_no_bounds_raises(self):
        with pytest.raises(ValidationError):
            TimeWindow()

    def test_start_only_validates(self):
        tw = TimeWindow(start_year=2015)
        assert tw.start_year == 2015
        assert tw.end_year is None

    def test_end_only_validates(self):
        tw = TimeWindow(end_year=2023)
        assert tw.end_year == 2023
        assert tw.start_year is None


class TestCandidateSetMinTwoSpecs:
    def test_one_spec_raises(self):
        with pytest.raises(ValidationError):
            CandidateSet(ordering="broadest_first", max_candidates=5, specs=[minimal_spec("s1")])

    def test_two_specs_validates(self):
        cs = CandidateSet(
            ordering="broadest_first",
            max_candidates=5,
            specs=[minimal_spec("s1"), minimal_spec("s2")],
        )
        assert len(cs.specs) == 2
