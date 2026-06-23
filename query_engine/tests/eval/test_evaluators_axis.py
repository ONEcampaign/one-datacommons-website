"""Tests for axis_classification evaluator."""
import copy
import sys

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1]))
from conftest import base_response, minimal_spec

from qre.eval.evaluators import axis_classification
from tests.eval.conftest import make_worked_example_response

_DEFINITE_EO = {"expected_status": "definite"}
_CANDIDATES_EO = {"expected_status": "candidates"}


@pytest.fixture
def worked_example_response():
    return make_worked_example_response()


def test_axis_classification_pass(worked_example_response):
    ev = axis_classification(output=worked_example_response, expected_output=_DEFINITE_EO)
    assert ev.value == 1.0, ev.comment


def test_axis_classification_skips_candidates():
    spec1 = minimal_spec("s1")
    spec2 = minimal_spec("s2")
    resp = base_response(
        status="candidates",
        candidates={"ordering": "broadest_first", "max_candidates": 5, "specs": [spec1, spec2]},
    )
    ev = axis_classification(output=resp, expected_output=_CANDIDATES_EO)
    assert ev == []


def test_axis_classification_valid_place_dcid(worked_example_response):
    # The worked example has a where-slot bound to country/ETH which is a valid place dcid.
    ev = axis_classification(output=worked_example_response, expected_output=_DEFINITE_EO)
    assert ev.value == 1.0


def test_axis_classification_non_place_where_slot(worked_example_response):
    resp = copy.deepcopy(worked_example_response)
    # Find the where-slot and replace country/ETH with a non-place dcid.
    for slot in resp["interpretation"]["slots"]:
        if slot["key"]["axis"] == "where":
            slot["binding"]["value"]["ref"]["dcid"] = "ODAGrants"  # a non-place dcid
            break
    ev = axis_classification(output=resp, expected_output=_DEFINITE_EO)
    assert ev.value == 0.0
    assert "ODAGrants" in (ev.comment or "")
