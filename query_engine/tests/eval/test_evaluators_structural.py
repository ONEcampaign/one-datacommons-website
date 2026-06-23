"""Tests for structural_conformance evaluator."""
import sys

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1]))
from conftest import base_response, minimal_spec

from qre.eval.evaluators import structural_conformance
from tests.eval.conftest import make_worked_example_response


@pytest.fixture
def worked_example_response():
    return make_worked_example_response()


def test_structural_valid_definite(worked_example_response):
    ev = structural_conformance(output=worked_example_response)
    assert ev.value == 1.0, ev.comment


def test_structural_valid_candidates():
    spec1 = minimal_spec("s1")
    spec2 = minimal_spec("s2")
    resp = base_response(
        status="candidates",
        candidates={
            "ordering": "broadest_first",
            "max_candidates": 5,
            "specs": [spec1, spec2],
        },
    )
    ev = structural_conformance(output=resp)
    assert ev.value == 1.0, ev.comment


def test_structural_valid_no_data():
    resp = base_response(
        status="no_data",
        no_data={"reason": "no_observations"},
    )
    ev = structural_conformance(output=resp)
    assert ev.value == 1.0, ev.comment


def test_structural_wrong_schema_version(worked_example_response):
    worked_example_response["schema_version"] = "2.0"
    ev = structural_conformance(output=worked_example_response)
    assert ev.value == 0.0
    assert "schema_version" in (ev.comment or "")


def test_structural_missing_field(worked_example_response):
    del worked_example_response["status"]
    ev = structural_conformance(output=worked_example_response)
    assert ev.value == 0.0


def test_structural_candidates_too_few_specs():
    spec1 = minimal_spec("s1")
    resp = base_response(
        status="candidates",
        candidates={
            "ordering": "broadest_first",
            "max_candidates": 5,
            "specs": [spec1],
        },
    )
    ev = structural_conformance(output=resp)
    assert ev.value == 0.0


def test_structural_not_a_dict():
    ev = structural_conformance(output="not a dict")
    assert ev.value == 0.0
    assert "not a dict" in (ev.comment or "")
