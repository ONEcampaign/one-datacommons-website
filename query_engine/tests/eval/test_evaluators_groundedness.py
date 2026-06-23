"""Tests for make_groundedness evaluator."""
import sys

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1]))

from qre.eval.evaluators import _iter_graphrefs, _parse_response, make_groundedness
from tests.eval.conftest import RaisingGraphClient, StubGraphClient, make_worked_example_response


@pytest.fixture
def worked_example_response():
    return make_worked_example_response()


def _all_dcids(output: dict) -> set[str]:
    resp = _parse_response(output)
    if resp is None:
        return set()
    return {ref.dcid for ref in _iter_graphrefs(resp)}


def test_groundedness_all_known(worked_example_response):
    dcids = _all_dcids(worked_example_response)
    assert dcids, "fixture should have at least one GraphRef"
    graph = StubGraphClient(known_dcids=dcids)
    ev = make_groundedness(graph)(output=worked_example_response)
    assert ev.value == 1.0, ev.comment
    assert ev.metadata["fabricated"] == 0
    assert ev.metadata["walked"] >= len(dcids)


def test_groundedness_one_missing(worked_example_response):
    dcids = _all_dcids(worked_example_response)
    assert len(dcids) >= 2, "need at least two dcids to remove one"
    missing = next(iter(dcids))
    graph = StubGraphClient(known_dcids=dcids - {missing})
    ev = make_groundedness(graph)(output=worked_example_response)
    assert ev.value == 0.0
    assert missing in (ev.comment or "")
    assert ev.metadata["fabricated"] >= 1


def test_groundedness_metadata_counts(worked_example_response):
    dcids = _all_dcids(worked_example_response)
    graph = StubGraphClient(known_dcids=set())
    ev = make_groundedness(graph)(output=worked_example_response)
    assert ev.value == 0.0
    assert ev.metadata["fabricated"] == ev.metadata["walked"]
    assert ev.metadata["walked"] >= len(dcids)


def test_groundedness_raises_on_graph_error(worked_example_response):
    graph = RaisingGraphClient()
    with pytest.raises(RuntimeError, match="graph error"):
        make_groundedness(graph)(output=worked_example_response)


def test_groundedness_invalid_response():
    graph = StubGraphClient(known_dcids={"any"})
    ev = make_groundedness(graph)(output={"schema_version": "1.0", "status": "bad"})
    assert ev.value == 0.0
    assert ev.metadata["walked"] == 0
