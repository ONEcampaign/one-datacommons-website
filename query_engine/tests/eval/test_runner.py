"""Tests for run_eval runner.

Uses a fake Langfuse client and in-process execution for all fixtures and stubs.
"""
from __future__ import annotations

import sys

import pytest
from langfuse.experiment import ExperimentItemResult, ExperimentResult

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parents[1]))

from qre import ResolveRequest, ResolveResponse
from qre.eval.runner import build_task, run_eval
from tests.eval.conftest import StubGraphClient, make_worked_example_response


class FakeDatasetItem:
    """Minimal mock of a Langfuse DatasetItem."""

    def __init__(self, inp: dict, expected_output: dict, metadata: dict):
        self.input = inp
        self.expected_output = expected_output
        self.metadata = metadata


class FakeDataset:
    """Fake dataset that executes task + evaluators in-process."""

    def __init__(self, items: list[FakeDatasetItem]):
        self.items = items

    def run_experiment(
        self, *, name, description=None, task, evaluators, run_evaluators, metadata=None
    ):
        item_results = []
        for item in self.items:
            output = task(item=item)
            item_evs = []
            for ev_fn in evaluators:
                result = ev_fn(
                    input=item.input,
                    output=output,
                    expected_output=item.expected_output,
                    metadata=item.metadata,
                )
                if isinstance(result, list):
                    item_evs.extend(result)
                else:
                    item_evs.append(result)
            item_results.append(
                ExperimentItemResult(
                    item=item,
                    output=output,
                    evaluations=item_evs,
                    trace_id=None,
                    dataset_run_id=None,
                )
            )

        run_evals = []
        for agg_fn in run_evaluators:
            ev = agg_fn(item_results=item_results)
            if isinstance(ev, list):
                run_evals.extend(ev)
            else:
                run_evals.append(ev)

        return ExperimentResult(
            name=name,
            run_name=name,
            description=description,
            item_results=item_results,
            run_evaluations=run_evals,
            experiment_id="fake-exp",
            dataset_run_id=None,
            dataset_run_url=None,
        )


class FakeLangfuse:
    """Fake Langfuse client with a pre-loaded dataset."""

    def __init__(self, dataset: FakeDataset):
        self._dataset = dataset

    def get_dataset(self, name: str) -> FakeDataset:
        return self._dataset


def _make_engine_task(response_dict: dict):
    """Return a task function that returns a fixed response."""
    def task(request: ResolveRequest) -> ResolveResponse:
        return ResolveResponse.model_validate(response_dict)
    return task


def _make_fake_item_from_worked_example():
    return FakeDatasetItem(
        inp={"query": "health ODA grants to Ethiopia since 2015", "entry_path": "raw_text"},
        expected_output={
            "expected_status": "definite",
            "expected_shape": {
                "population_type_dcid": "DevelopmentFinance",
                "measured_property_dcid": "DevelopmentFinanceFlow",
                "stat_type_dcid": "measuredValue",
                "measurement_qualifier_dcid": None,
                "measurement_denominator_dcid": None,
            },
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
            "candidate_count": None,
        },
        metadata={
            "id": "df-06",
            "slice": "main",
            "tags": [{"behaviour": "definite"}],
            "status": "VERIFIED_AGAINST_GRAPH",
            "notes": "",
        },
    )


def test_run_eval_wires_task_and_evaluators():
    we = make_worked_example_response()
    item = _make_fake_item_from_worked_example()

    # All dcids present in the worked example.
    from qre.eval.evaluators import _iter_graphrefs, _parse_response
    dcids = {ref.dcid for ref in _iter_graphrefs(_parse_response(we))}
    graph = StubGraphClient(known_dcids=dcids)

    dataset = FakeDataset([item])
    client = FakeLangfuse(dataset)
    task = _make_engine_task(we)

    result = run_eval(
        task,
        dataset_name="test-ds",
        engine_build="test-build",
        graph=graph,
        langfuse=client,
    )
    assert result is not None
    run_names = {ev.name for ev in result.run_evaluations}
    assert "structural_conformance_rate" in run_names
    assert "fabricated_ref_rate" in run_names


def test_build_task_raw_text():
    we = make_worked_example_response()
    task_fn = _make_engine_task(we)
    lf_task = build_task(task_fn)

    item = FakeDatasetItem(
        inp={"query": "health ODA", "entry_path": "raw_text"},
        expected_output={},
        metadata={},
    )
    output = lf_task(item=item)
    assert output["schema_version"] == "1.0"
    assert output["status"] == "definite"


def test_build_task_spec_resubmit_does_not_raise():
    """build_task's spec_resubmit branch must succeed when item.input carries shape_id+slots.

    Regression guard for the KeyError that fired before golden_to_item() was fixed to
    copy shape_id/slots into input — item.input was missing those keys so the runner
    blew up before the engine ran.
    """
    we = make_worked_example_response()
    task_fn = _make_engine_task(we)
    lf_task = build_task(task_fn)

    slots = [
        {
            "key": {
                "axis": "what",
                "property": {"dcid": "DevelopmentFinanceScheme", "label": "Scheme"},
                "label": "scheme",
            },
            "binding": {
                "kind": "value",
                "value": {
                    "ref": {"dcid": "ODAGrants", "label": "ODA Grants"},
                    "value_kind": "enum_value",
                    "time_window": None,
                    "literal": None,
                },
            },
        }
    ]
    item = FakeDatasetItem(
        inp={
            "query": "spec_resubmit: health ODA grants to Ethiopia",
            "entry_path": "spec_resubmit",
            "shape_id": "dev_finance_crs_dac",
            "slots": slots,
            "stat_var_dcids": None,
            "entity_dcids": None,
        },
        expected_output={},
        metadata={},
    )
    # Must not raise KeyError (pre-fix) or ValueError (unsupported path).
    output = lf_task(item=item)
    assert "schema_version" in output


def test_build_task_unsupported_entry_path():
    we = make_worked_example_response()
    task_fn = _make_engine_task(we)
    lf_task = build_task(task_fn)

    item = FakeDatasetItem(
        inp={"query": "q", "entry_path": "parsed"},
        expected_output={},
        metadata={},
    )
    with pytest.raises(ValueError, match="Unsupported entry_path"):
        lf_task(item=item)
