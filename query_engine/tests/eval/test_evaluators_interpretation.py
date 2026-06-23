"""Tests for interpretation_match evaluator."""
import copy
import sys

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1]))
from conftest import base_response, minimal_spec

from qre.eval.evaluators import interpretation_match
from tests.eval.conftest import make_worked_example_response

# Golden expectations derived from the df-06 worked example in conftest.py.
# (health ODA grants to Ethiopia -- one entity, directional recipient)
_DF01_EXPECTED = {
    "expected_status": "definite",
    "expected_shape": {
        "population_type_dcid": "DevelopmentFinance",
        "measured_property_dcid": "DevelopmentFinanceFlow",
        "stat_type_dcid": "measuredValue",
        "measurement_qualifier_dcid": None,
        "measurement_denominator_dcid": None,
    },
    # The corpus only lists the slots the expert verified; when/source are intentionally omitted.
    "expected_slots": [
        {
            "axis": "what",
            "property_dcid": "DevelopmentFinanceScheme",
            "binding_kind": "value",
            "value_dcid": "ODAGrants",
        },
        {
            "axis": "how",
            "property_dcid": "DevelopmentFinancePurpose",
            "binding_kind": "value",
            "value_dcid": "DAC/Health",
        },
        {
            "axis": "where",
            "property_dcid": "DevelopmentFinanceRecipient",
            "binding_kind": "value",
            "value_dcid": "country/ETH",
        },
    ],
    "expected_stat_vars": ["ONE/CRS_DAC/Health-ODAGrants-ETH"],
    "expected_entities": [
        {
            "dcid": "country/ETH",
            "role_kind": "directional",
            "direction": "to",
            "role_dcid": "DevelopmentFinanceRecipient",
        },
    ],
    "expected_no_data_reason": None,
}


@pytest.fixture
def worked_example_response():
    return make_worked_example_response()


def test_interpretation_match_worked_example(worked_example_response):
    ev = interpretation_match(
        output=worked_example_response,
        expected_output=_DF01_EXPECTED,
    )
    assert ev.value == 1.0, ev.comment


def test_interpretation_match_skip_candidates():
    spec1 = minimal_spec("s1")
    spec2 = minimal_spec("s2")
    resp = base_response(
        status="candidates",
        candidates={"ordering": "broadest_first", "max_candidates": 5, "specs": [spec1, spec2]},
    )
    ev = interpretation_match(
        output=resp,
        expected_output={"expected_status": "candidates"},
    )
    assert ev.value is None


def test_interpretation_match_skip_no_data():
    resp = base_response(status="no_data", no_data={"reason": "no_observations"})
    ev = interpretation_match(
        output=resp,
        expected_output={"expected_status": "no_data"},
    )
    assert ev.value is None


def test_interpretation_match_wrong_shape(worked_example_response):
    wrong_expected = copy.deepcopy(_DF01_EXPECTED)
    wrong_expected["expected_shape"]["population_type_dcid"] = "WrongType"
    ev = interpretation_match(output=worked_example_response, expected_output=wrong_expected)
    assert ev.value == 0.0
    assert "shape" in (ev.comment or "").lower()


def test_interpretation_match_wrong_stat_var(worked_example_response):
    wrong_expected = copy.deepcopy(_DF01_EXPECTED)
    wrong_expected["expected_stat_vars"] = ["wrong/SV"]
    ev = interpretation_match(output=worked_example_response, expected_output=wrong_expected)
    assert ev.value == 0.0
    assert "stat_var" in (ev.comment or "")


def test_interpretation_match_wrong_entity(worked_example_response):
    wrong_expected = copy.deepcopy(_DF01_EXPECTED)
    wrong_expected["expected_entities"] = [
        {
            "dcid": "country/KEN",
            "role_kind": "directional",
            "direction": "to",
            "role_dcid": "DevelopmentFinanceRecipient",
        }
    ]
    ev = interpretation_match(output=worked_example_response, expected_output=wrong_expected)
    assert ev.value == 0.0
    assert "entity" in (ev.comment or "")


def test_interpretation_match_subject_with_direction(worked_example_response):
    # EntityRoleSubject has no direction field in the schema; test ensures
    # evaluator nulls out direction/role_dcid for subjects during comparison.
    import copy as _copy

    resp = _copy.deepcopy(worked_example_response)
    resp["interpretation"]["entities"].append({
        "ref": {"dcid": "country/USA", "label": "USA"},
        "entity_type": {"dcid": "Country", "label": "Country"},
        "role": {"kind": "subject"},
    })
    expected = _copy.deepcopy(_DF01_EXPECTED)
    expected["expected_entities"] = [
        {
            "dcid": "country/ETH",
            "role_kind": "directional",
            "direction": "to",
            "role_dcid": "DevelopmentFinanceRecipient",
        },
        {"dcid": "country/USA", "role_kind": "subject", "direction": "from", "role_dcid": None},
    ]
    ev = interpretation_match(output=resp, expected_output=expected)
    assert ev.value == 1.0, ev.comment


def test_interpretation_match_wrong_slot_binding(worked_example_response):
    wrong_expected = copy.deepcopy(_DF01_EXPECTED)
    # Flip the what-slot to unbound to force a mismatch.
    wrong_expected["expected_slots"][0]["binding_kind"] = "unbound"
    wrong_expected["expected_slots"][0]["value_dcid"] = None
    ev = interpretation_match(output=worked_example_response, expected_output=wrong_expected)
    assert ev.value == 0.0
    assert "slot" in (ev.comment or "")


_SCHEME_PROP_REF = {"dcid": "DevelopmentFinanceScheme", "label": "flow type"}
_PURPOSE_PROP_REF = {"dcid": "DevelopmentFinancePurpose", "label": "purpose"}
_RECIPIENT_PROP_REF = {"dcid": "DevelopmentFinanceRecipient", "label": "recipient"}
_ETH_REF = {"dcid": "country/ETH", "label": "Ethiopia"}
_ODA_GRANTS_REF = {"dcid": "ODAGrants", "label": "ODA grants"}
_DAC_HEALTH_REF = {"dcid": "DAC/Health", "label": "health (total)"}


def test_interpretation_match_spurious_value_bound_slot_scores_zero(worked_example_response):
    """Spurious value-bound slot not in golden scores 0.0."""
    resp = copy.deepcopy(worked_example_response)
    resp["interpretation"]["slots"].append({
        "key": {
            "axis": "how",
            "property": {"dcid": "SpuriousProp", "label": "extra"},
            "label": "extra",
        },
        "binding": {
            "kind": "value",
            "value": {"ref": {"dcid": "SomeValue", "label": "val"}, "value_kind": "enum_value"},
        },
    })
    ev = interpretation_match(output=resp, expected_output=_DF01_EXPECTED)
    assert ev.value == 0.0, ev.comment
    assert "spurious" in (ev.comment or "")


def test_interpretation_match_extra_unbound_slot_still_passes(worked_example_response):
    """Extra unbound and absent slots not in golden still pass evaluation."""
    resp = copy.deepcopy(worked_example_response)
    resp["interpretation"]["slots"].append({
        "key": {
            "axis": "source",
            "property": {"dcid": "ExtraProp", "label": "extra"},
            "label": "extra",
        },
        "binding": {"kind": "unbound"},
    })
    resp["interpretation"]["slots"].append({
        "key": {
            "axis": "when",
            "property": {"dcid": "AnotherProp", "label": "another"},
            "label": "another",
        },
        "binding": {"kind": "absent"},
    })
    ev = interpretation_match(output=resp, expected_output=_DF01_EXPECTED)
    assert ev.value == 1.0, ev.comment
