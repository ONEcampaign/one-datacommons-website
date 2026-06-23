"""Round-trip and discrimination tests for the contract response and request models."""
import pytest
from pydantic import ValidationError

from qre import (
    CandidatesResponse,
    DefiniteResponse,
    NoDataResponse,
    ParsedInput,
    RawTextInput,
    ResolveRequest,
    ResolveResponse,
    Spec,
    SpecResubmitInput,
)
from tests.conftest import base_response, minimal_spec


class TestWorkedExample:
    def test_worked_example_validates(self, worked_example_response):
        resp = ResolveResponse.model_validate(worked_example_response)
        assert isinstance(resp.root, DefiniteResponse)
        assert resp.root.status == "definite"
        assert isinstance(resp.root.interpretation, Spec)

    def test_roundtrip_stable(self, worked_example_response):
        resp = ResolveResponse.model_validate(worked_example_response)
        dumped = resp.model_dump(mode="json")
        resp2 = ResolveResponse.model_validate(dumped)
        assert resp2.model_dump(mode="json") == dumped


class TestThreeVariants:
    def test_definite_discriminates(self):
        payload = base_response(status="definite", interpretation=minimal_spec())
        resp = ResolveResponse.model_validate(payload)
        assert isinstance(resp.root, DefiniteResponse)

    def test_candidates_discriminates(self):
        payload = base_response(
            status="candidates",
            candidates={
                "ordering": "broadest_first",
                "max_candidates": 2,
                "specs": [minimal_spec("s1"), minimal_spec("s2")],
            },
        )
        resp = ResolveResponse.model_validate(payload)
        assert isinstance(resp.root, CandidatesResponse)

    def test_no_data_discriminates(self):
        payload = base_response(
            status="no_data",
            no_data={"reason": "no_observations"},
        )
        resp = ResolveResponse.model_validate(payload)
        assert isinstance(resp.root, NoDataResponse)

    def test_wrong_body_raises(self):
        # status says candidates but the body has a definite shape
        payload = base_response(
            status="candidates",
            interpretation=minimal_spec(),  # wrong field for candidates
        )
        with pytest.raises(ValidationError):
            ResolveResponse.model_validate(payload)


class TestRequestInputDiscrimination:
    def test_raw_text_arm(self):
        req = ResolveRequest.model_validate(
            {"input": {"kind": "raw_text", "query": "ODA to Ethiopia"}}
        )
        assert isinstance(req.input, RawTextInput)

    def test_parsed_arm(self):
        req = ResolveRequest.model_validate(
            {"input": {"kind": "parsed", "variable_text": ["ODA"]}}
        )
        assert isinstance(req.input, ParsedInput)

    def test_spec_resubmit_arm(self):
        req = ResolveRequest.model_validate(
            {"input": {"kind": "spec_resubmit", "shape_id": "df-flow", "slots": []}}
        )
        assert isinstance(req.input, SpecResubmitInput)

    def test_unknown_kind_raises(self):
        with pytest.raises(ValidationError):
            ResolveRequest.model_validate(
                {"input": {"kind": "nonexistent", "query": "test"}}
            )
