"""Tests for behaviour_by_tag evaluator."""
import sys

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1]))
from conftest import base_response, minimal_spec

from qre.eval.evaluators import behaviour_by_tag
from tests.eval.conftest import make_worked_example_response


def _meta(behaviour: str) -> dict:
    return {"tags": [{"behaviour": behaviour}]}


@pytest.fixture
def worked_example_response():
    return make_worked_example_response()


def _get_ev(evs, name: str):
    for e in evs:
        if e.name == name:
            return e
    return None


def test_behaviour_definite_pass(worked_example_response):
    evs = behaviour_by_tag(
        output=worked_example_response,
        expected_output={"expected_status": "definite"},
        metadata=_meta("definite"),
    )
    assert _get_ev(evs, "behaviour_match_definite").value == 1.0
    assert _get_ev(evs, "behaviour_match_candidates").value is None
    assert _get_ev(evs, "behaviour_match_no_data").value is None


def test_behaviour_definite_fail():
    resp = base_response(status="no_data", no_data={"reason": "no_observations"})
    evs = behaviour_by_tag(
        output=resp,
        expected_output={"expected_status": "definite"},
        metadata=_meta("definite"),
    )
    assert _get_ev(evs, "behaviour_match_definite").value == 0.0


def test_behaviour_candidates_pass():
    spec1 = minimal_spec("s1")
    spec2 = minimal_spec("s2")
    resp = base_response(
        status="candidates",
        candidates={"ordering": "broadest_first", "max_candidates": 5, "specs": [spec1, spec2]},
    )
    evs = behaviour_by_tag(
        output=resp,
        expected_output={"expected_status": "candidates"},
        metadata=_meta("candidates"),
    )
    assert _get_ev(evs, "behaviour_match_candidates").value == 1.0
    assert _get_ev(evs, "behaviour_match_definite").value is None


def test_behaviour_candidates_one_spec_fails():
    spec1 = minimal_spec("s1")
    resp = base_response(
        status="candidates",
        candidates={"ordering": "broadest_first", "max_candidates": 5, "specs": [spec1]},
    )
    evs = behaviour_by_tag(
        output=resp,
        expected_output={"expected_status": "candidates"},
        metadata=_meta("candidates"),
    )
    assert _get_ev(evs, "behaviour_match_candidates").value == 0.0


def test_behaviour_candidates_duplicate_spec_id():
    spec1 = minimal_spec("s1")
    spec2 = minimal_spec("s1")
    resp = base_response(
        status="candidates",
        candidates={"ordering": "broadest_first", "max_candidates": 5, "specs": [spec1, spec2]},
    )
    evs = behaviour_by_tag(
        output=resp,
        expected_output={"expected_status": "candidates"},
        metadata=_meta("candidates"),
    )
    assert _get_ev(evs, "behaviour_match_candidates").value == 0.0


def test_behaviour_no_data_pass():
    resp = base_response(status="no_data", no_data={"reason": "no_observations"})
    evs = behaviour_by_tag(
        output=resp,
        expected_output={
            "expected_status": "no_data",
            "expected_no_data_reason": "no_observations",
        },
        metadata=_meta("no_data"),
    )
    assert _get_ev(evs, "behaviour_match_no_data").value == 1.0
    assert _get_ev(evs, "behaviour_match_definite").value is None


def test_behaviour_no_data_wrong_reason():
    resp = base_response(status="no_data", no_data={"reason": "no_observations"})
    evs = behaviour_by_tag(
        output=resp,
        expected_output={
            "expected_status": "no_data",
            "expected_no_data_reason": "entity_not_resolved",
        },
        metadata=_meta("no_data"),
    )
    assert _get_ev(evs, "behaviour_match_no_data").value == 0.0


def test_behaviour_no_data_all_reasons():
    for reason in (
        "no_observations",
        "entity_not_resolved",
        "variable_not_resolved",
        "denominator_not_available",
    ):
        resp = base_response(status="no_data", no_data={"reason": reason})
        evs = behaviour_by_tag(
            output=resp,
            expected_output={"expected_status": "no_data", "expected_no_data_reason": reason},
            metadata=_meta("no_data"),
        )
        assert _get_ev(evs, "behaviour_match_no_data").value == 1.0, reason
