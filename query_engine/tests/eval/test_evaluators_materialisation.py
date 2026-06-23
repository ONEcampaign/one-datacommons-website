"""Tests for make_materialisation evaluator."""
import copy
import sys

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1]))

from qre.eval.evaluators import make_materialisation
from tests.eval.conftest import RaisingGraphClient, StubGraphClient, make_worked_example_response

_DEFINITE_EO = {"expected_status": "definite"}
_CANDIDATES_EO = {"expected_status": "candidates"}


@pytest.fixture
def worked_example_response():
    return make_worked_example_response()


def test_materialisation_skips_candidates():
    from conftest import base_response, minimal_spec
    spec1 = minimal_spec("s1")
    spec2 = minimal_spec("s2")
    resp = base_response(
        status="candidates",
        candidates={"ordering": "broadest_first", "max_candidates": 5, "specs": [spec1, spec2]},
    )
    ev = make_materialisation(StubGraphClient())(output=resp, expected_output=_CANDIDATES_EO)
    assert ev == []


def test_materialisation_breadth_pass(worked_example_response):
    ev = make_materialisation(StubGraphClient())(
        output=worked_example_response, expected_output=_DEFINITE_EO
    )
    assert ev.value == 1.0, ev.comment


def test_materialisation_breadth_zero_count(worked_example_response):
    resp = copy.deepcopy(worked_example_response)
    resp["interpretation"]["coverage"]["dimensions"][0]["count"] = 0
    ev = make_materialisation(StubGraphClient())(output=resp, expected_output=_DEFINITE_EO)
    assert ev.value == 0.0
    assert "count<=0" in (ev.comment or "")


def test_materialisation_breadth_no_data(worked_example_response):
    resp = copy.deepcopy(worked_example_response)
    resp["interpretation"]["coverage"]["has_data"] = False
    ev = make_materialisation(StubGraphClient())(output=resp, expected_output=_DEFINITE_EO)
    assert ev.value == 0.0


def _exact_response():
    from conftest import base_response, minimal_spec
    spec = copy.deepcopy(minimal_spec("s1"))
    spec["coverage"] = {
        "kind": "exact",
        "has_data": True,
        "observation_count": 100,
        "window": {"start_year": 2015},
    }
    spec["stat_vars"] = [
        {"ref": {"dcid": "sv1", "label": "sv1"}, "shape_id": "sh1", "slot_values": []}
    ]
    spec["entities"] = [
        {"ref": {"dcid": "country/ETH", "label": "Ethiopia"}, "role": {"kind": "subject"}}
    ]
    return base_response(status="definite", interpretation=spec)


def test_materialisation_exact_within_tolerance():
    resp = _exact_response()
    key = (frozenset(["sv1"]), frozenset(["country/ETH"]))
    graph = StubGraphClient(counts={key: 98})  # within 5% of 100
    ev = make_materialisation(graph)(output=resp, expected_output=_DEFINITE_EO)
    assert ev.value == 1.0, ev.comment


def test_materialisation_exact_outside_tolerance():
    resp = _exact_response()
    key = (frozenset(["sv1"]), frozenset(["country/ETH"]))
    graph = StubGraphClient(counts={key: 50})  # 50% off
    ev = make_materialisation(graph)(output=resp, expected_output=_DEFINITE_EO)
    assert ev.value == 0.0
    assert "count mismatch" in (ev.comment or "")


def test_materialisation_exact_graph_returns_none():
    resp = _exact_response()
    graph = StubGraphClient(counts={})  # no entry -> None
    ev = make_materialisation(graph)(output=resp, expected_output=_DEFINITE_EO)
    assert ev.value == 1.0
    assert "skipping" in (ev.comment or "")


def test_materialisation_exact_raises_on_graph_error():
    resp = _exact_response()
    with pytest.raises(RuntimeError, match="graph error"):
        make_materialisation(RaisingGraphClient())(output=resp, expected_output=_DEFINITE_EO)


def _bare_response(has_data: bool):
    from conftest import base_response, minimal_spec
    spec = copy.deepcopy(minimal_spec("s1"))
    spec["coverage"] = {"kind": "bare", "has_data": has_data}
    return base_response(status="definite", interpretation=spec)


def test_materialisation_bare_pass():
    ev = make_materialisation(StubGraphClient())(
        output=_bare_response(True), expected_output=_DEFINITE_EO
    )
    assert ev.value == 1.0


def test_materialisation_bare_no_data():
    ev = make_materialisation(StubGraphClient())(
        output=_bare_response(False), expected_output=_DEFINITE_EO
    )
    assert ev.value == 0.0
