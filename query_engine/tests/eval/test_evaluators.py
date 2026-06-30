"""Tests for evaluators: entry_path_audit, conjunction_honesty, and ordering.

Also covers the spec_resubmit branch in runner.build_task.
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1]))
from conftest import base_response, minimal_spec

from qre import ResolveRequest, ResolveResponse
from qre.eval.evaluators import (
    behaviour_by_tag,
    conjunction_honesty,
    entry_path_audit,
)
from qre.eval.runner import build_task

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec_with_pipeline(spec_id: str, pipeline_trace: list[dict], member_count: int = 1) -> dict:
    """A minimal Spec dict with a custom pipeline_trace and member_count."""
    spec = minimal_spec(spec_id)
    spec["resolution"]["pipeline_trace"] = pipeline_trace
    spec["shape"]["member_count"] = member_count
    return spec


def _definite_response(
    *,
    entry_path: str = "raw_text",
    extract_skipped: bool = False,
    pipeline_trace: list[dict] | None = None,
    warnings: list[dict] | None = None,
    additional_interpretations=None,
    spec_id: str = "s1",
    member_count: int = 1,
) -> dict:
    """Build a minimal definite response, overriding only the fields under test."""
    trace = pipeline_trace if pipeline_trace is not None else [
        {"step": "extract", "ran": True},
        {"step": "recall", "ran": True},
        {"step": "shape", "ran": True},
        {"step": "bind", "ran": True},
        {"step": "materialise", "ran": True},
        {"step": "answer", "ran": True},
    ]
    spec = _spec_with_pipeline(spec_id, trace, member_count)
    resp = {
        "schema_version": "1.0",
        "status": "definite",
        "query_echo": {
            "entry_path": entry_path,
            "variable_text": ["test"],
            "extract_skipped": extract_skipped,
        },
        "diagnostics": {
            "engine_build": "test-build",
            "warnings": warnings if warnings is not None else [],
        },
        "interpretation": spec,
    }
    if additional_interpretations is not None:
        resp["additional_interpretations"] = additional_interpretations
    return resp


def _no_data_response(
    *,
    entry_path: str = "raw_text",
    extract_skipped: bool = False,
) -> dict:
    return {
        "schema_version": "1.0",
        "status": "no_data",
        "query_echo": {
            "entry_path": entry_path,
            "variable_text": ["test"],
            "extract_skipped": extract_skipped,
        },
        "diagnostics": {"engine_build": "test-build", "warnings": []},
        "no_data": {"reason": "variable_not_resolved"},
    }


def _candidates_response(specs: list[dict]) -> dict:
    return base_response(
        status="candidates",
        candidates={
            "ordering": "broadest_first",
            "max_candidates": 5,
            "specs": specs,
        },
    )


def _conj_meta(conj_tag: str) -> dict:
    return {"tags": [{"conjunction": conj_tag}]}


def _cross_shape_warning() -> dict:
    return {"code": "CONJUNCTION_CROSS_SHAPE", "severity": "warn", "message": "cross-shape"}


# ---------------------------------------------------------------------------
# entry_path_audit
# ---------------------------------------------------------------------------


class TestEntryPathAudit:
    def test_raw_text_returns_empty(self):
        resp = _definite_response(entry_path="raw_text", extract_skipped=False)
        result = entry_path_audit(input={"entry_path": "raw_text"}, output=resp, expected_output={})
        assert result == []

    def test_raw_text_default_returns_empty(self):
        resp = _definite_response(entry_path="raw_text")
        result = entry_path_audit(input={}, output=resp, expected_output={})
        assert result == []

    def test_spec_resubmit_pass_definite(self):
        trace = [
            {"step": "extract", "ran": False},
            {"step": "recall", "ran": True},
            {"step": "bind", "ran": True},
            {"step": "materialise", "ran": True},
            {"step": "answer", "ran": True},
        ]
        resp = _definite_response(
            entry_path="spec_resubmit",
            extract_skipped=True,
            pipeline_trace=trace,
        )
        result = entry_path_audit(
            input={"entry_path": "spec_resubmit"},
            output=resp,
            expected_output={},
        )
        assert result.name == "entry_path_audit"
        assert result.value == 1.0

    def test_parsed_pass_definite(self):
        trace = [{"step": "extract", "ran": False}, {"step": "answer", "ran": True}]
        resp = _definite_response(
            entry_path="parsed",
            extract_skipped=True,
            pipeline_trace=trace,
        )
        result = entry_path_audit(
            input={"entry_path": "parsed"},
            output=resp,
            expected_output={},
        )
        assert result.value == 1.0

    def test_spec_resubmit_fail_extract_not_skipped(self):
        trace = [{"step": "extract", "ran": False}, {"step": "answer", "ran": True}]
        resp = _definite_response(
            entry_path="spec_resubmit",
            extract_skipped=False,  # wrong
            pipeline_trace=trace,
        )
        result = entry_path_audit(
            input={"entry_path": "spec_resubmit"},
            output=resp,
            expected_output={},
        )
        assert result.value == 0.0
        assert "extract_skipped" in result.comment

    def test_spec_resubmit_fail_no_extract_skip_in_trace(self):
        # extract_skipped is True but pipeline_trace has extract with ran=True
        trace = [
            {"step": "extract", "ran": True},  # ran — wrong
            {"step": "answer", "ran": True},
        ]
        resp = _definite_response(
            entry_path="spec_resubmit",
            extract_skipped=True,
            pipeline_trace=trace,
        )
        result = entry_path_audit(
            input={"entry_path": "spec_resubmit"},
            output=resp,
            expected_output={},
        )
        assert result.value == 0.0
        assert "pipeline_trace" in result.comment

    def test_spec_resubmit_pass_no_data(self):
        resp = _no_data_response(entry_path="spec_resubmit", extract_skipped=True)
        result = entry_path_audit(
            input={"entry_path": "spec_resubmit"},
            output=resp,
            expected_output={},
        )
        assert result.value == 1.0

    def test_spec_resubmit_fail_no_data_extract_not_skipped(self):
        resp = _no_data_response(entry_path="spec_resubmit", extract_skipped=False)
        result = entry_path_audit(
            input={"entry_path": "spec_resubmit"},
            output=resp,
            expected_output={},
        )
        assert result.value == 0.0

    def test_invalid_response_returns_zero(self):
        result = entry_path_audit(
            input={"entry_path": "spec_resubmit"},
            output={"not": "valid"},
            expected_output={},
        )
        assert result.value == 0.0


# ---------------------------------------------------------------------------
# conjunction_honesty
# ---------------------------------------------------------------------------


class TestConjunctionHonesty:
    def test_non_cross_shape_returns_empty(self):
        resp = _definite_response()
        result = conjunction_honesty(
            output=resp,
            metadata=_conj_meta("none"),
            expected_output={},
        )
        assert result == []

    def test_no_conjunction_tag_returns_empty(self):
        resp = _definite_response()
        result = conjunction_honesty(
            output=resp,
            metadata={"tags": [{"behaviour": "definite"}]},
            expected_output={},
        )
        assert result == []

    def test_cross_shape_pass_empty_additional(self):
        resp = _definite_response(
            warnings=[_cross_shape_warning()],
            additional_interpretations=[],
        )
        result = conjunction_honesty(
            output=resp,
            metadata=_conj_meta("cross_shape"),
            expected_output={},
        )
        assert result.value == 1.0

    def test_cross_shape_pass_with_additional_specs(self):
        extra_spec = minimal_spec("s2")
        resp = _definite_response(
            warnings=[_cross_shape_warning()],
            additional_interpretations=[extra_spec],
        )
        result = conjunction_honesty(
            output=resp,
            metadata=_conj_meta("cross_shape"),
            expected_output={},
        )
        assert result.value == 1.0

    def test_cross_shape_fail_missing_warning(self):
        resp = _definite_response(
            warnings=[],  # missing CONJUNCTION_CROSS_SHAPE
            additional_interpretations=[],
        )
        result = conjunction_honesty(
            output=resp,
            metadata=_conj_meta("cross_shape"),
            expected_output={},
        )
        assert result.value == 0.0
        assert "CONJUNCTION_CROSS_SHAPE" in result.comment

    def test_cross_shape_fail_additional_interpretations_none(self):
        resp = _definite_response(
            warnings=[_cross_shape_warning()],
            # additional_interpretations not set → None in the model
        )
        result = conjunction_honesty(
            output=resp,
            metadata=_conj_meta("cross_shape"),
            expected_output={},
        )
        assert result.value == 0.0
        assert "None" in result.comment

    def test_cross_shape_fail_non_definite_response(self):
        resp = _no_data_response()
        result = conjunction_honesty(
            output=resp,
            metadata=_conj_meta("cross_shape"),
            expected_output={},
        )
        assert result.value == 0.0
        assert "no_data" in result.comment

    def test_cross_shape_fail_invalid_response(self):
        result = conjunction_honesty(
            output={"bad": "response"},
            metadata=_conj_meta("cross_shape"),
            expected_output={},
        )
        assert result.value == 0.0


# ---------------------------------------------------------------------------
# _score_candidates ordering (F12)
# ---------------------------------------------------------------------------


class TestCandidatesOrdering:
    def test_ordered_broadest_first_passes(self):
        # s1 has member_count=5, s2 has member_count=2; s1 must come first
        spec1 = _spec_with_pipeline("s1", [], member_count=5)
        spec2 = _spec_with_pipeline("s2", [], member_count=2)
        resp = _candidates_response([spec1, spec2])
        evs = behaviour_by_tag(
            output=resp,
            expected_output={"expected_status": "candidates"},
            metadata={"tags": [{"behaviour": "candidates"}]},
        )
        ev = next(e for e in evs if e.name == "behaviour_match_candidates")
        assert ev.value == 1.0

    def test_ordered_tiebreak_spec_id_passes(self):
        # equal member_count: spec_id "a" < "b" so "a" must come first
        spec1 = _spec_with_pipeline("a", [], member_count=3)
        spec2 = _spec_with_pipeline("b", [], member_count=3)
        resp = _candidates_response([spec1, spec2])
        evs = behaviour_by_tag(
            output=resp,
            expected_output={"expected_status": "candidates"},
            metadata={"tags": [{"behaviour": "candidates"}]},
        )
        ev = next(e for e in evs if e.name == "behaviour_match_candidates")
        assert ev.value == 1.0

    def test_wrong_order_fails(self):
        # s2 (member_count=2) before s1 (member_count=5) — reversed order
        spec1 = _spec_with_pipeline("s1", [], member_count=5)
        spec2 = _spec_with_pipeline("s2", [], member_count=2)
        resp = _candidates_response([spec2, spec1])  # wrong order
        evs = behaviour_by_tag(
            output=resp,
            expected_output={"expected_status": "candidates"},
            metadata={"tags": [{"behaviour": "candidates"}]},
        )
        ev = next(e for e in evs if e.name == "behaviour_match_candidates")
        assert ev.value == 0.0

    def test_tiebreak_wrong_order_fails(self):
        # equal member_count: "b" before "a" — wrong spec_id tiebreak order
        spec1 = _spec_with_pipeline("a", [], member_count=3)
        spec2 = _spec_with_pipeline("b", [], member_count=3)
        resp = _candidates_response([spec2, spec1])  # b before a — wrong
        evs = behaviour_by_tag(
            output=resp,
            expected_output={"expected_status": "candidates"},
            metadata={"tags": [{"behaviour": "candidates"}]},
        )
        ev = next(e for e in evs if e.name == "behaviour_match_candidates")
        assert ev.value == 0.0


# ---------------------------------------------------------------------------
# runner.build_task — spec_resubmit dispatch
# ---------------------------------------------------------------------------


class FakeDatasetItem:
    """Minimal mock of a Langfuse DatasetItem."""

    def __init__(self, inp: dict):
        self.input = inp
        self.expected_output = {}
        self.metadata = {}


class TestBuildTaskSpecResubmit:
    def _make_engine_task(self, response_dict: dict):
        """Return a task that captures the last ResolveRequest and returns a fixed response."""
        captured = {}

        def task(request: ResolveRequest) -> ResolveResponse:
            captured["request"] = request
            return ResolveResponse.model_validate(response_dict)

        return task, captured

    def _worked_example_response(self) -> dict:
        from tests.eval.conftest import make_worked_example_response
        return make_worked_example_response()

    def test_spec_resubmit_dispatches_correctly(self):
        we = self._worked_example_response()
        task, captured = self._make_engine_task(we)
        lf_task = build_task(task)

        item = FakeDatasetItem(
            inp={
                "entry_path": "spec_resubmit",
                "shape_id": "dev_finance_crs_dac",
                "slots": [
                    {
                        "key": {"axis": "what", "label": "flow type"},
                        "binding": {
                            "kind": "value",
                            "value": {
                                "ref": {"dcid": "ODAGrants", "label": "ODA grants"},
                                "value_kind": "enum_value",
                            },
                        },
                    }
                ],
            }
        )
        output = lf_task(item=item)
        assert output["schema_version"] == "1.0"

        req = captured["request"]
        assert req.input.kind == "spec_resubmit"
        assert req.input.shape_id == "dev_finance_crs_dac"

    def test_unsupported_entry_path_raises(self):
        we = self._worked_example_response()
        task, _ = self._make_engine_task(we)
        lf_task = build_task(task)

        item = FakeDatasetItem(inp={"entry_path": "parsed", "query": "q"})
        with pytest.raises(ValueError, match="Unsupported entry_path"):
            lf_task(item=item)

    def test_raw_text_still_works(self):
        we = self._worked_example_response()
        task, captured = self._make_engine_task(we)
        lf_task = build_task(task)

        item = FakeDatasetItem(inp={"entry_path": "raw_text", "query": "health ODA"})
        output = lf_task(item=item)
        assert output["schema_version"] == "1.0"
        assert captured["request"].input.kind == "raw_text"
