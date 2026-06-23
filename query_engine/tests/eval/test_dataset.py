"""Tests for dataset.py -- pure mapping functions, no network."""
import json
from pathlib import Path

import pytest

from qre.eval.dataset import golden_to_item, item_id_for, sync_dataset

_GOLDENS_PATH = Path(__file__).resolve().parents[2] / "goldens.json"


@pytest.fixture(scope="module")
def all_goldens():
    with open(_GOLDENS_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def test_golden_to_item_top_level_keys(all_goldens):
    for g in all_goldens:
        item = golden_to_item(g)
        assert set(item.keys()) == {"input", "expected_output", "metadata"}, g["id"]


def test_golden_to_item_input_fields(all_goldens):
    for g in all_goldens:
        inp = golden_to_item(g)["input"]
        assert "query" in inp, g["id"]
        assert "entry_path" in inp, g["id"]
        assert inp["query"] == g["query"]
        assert inp["entry_path"] == g["entry_path"]


def test_golden_to_item_expected_output_fields(all_goldens):
    required = {
        "expected_status",
        "expected_shape",
        "expected_slots",
        "expected_stat_vars",
        "expected_entities",
        "expected_no_data_reason",
        "candidate_count",
    }
    for g in all_goldens:
        eo = golden_to_item(g)["expected_output"]
        assert set(eo.keys()) == required, g["id"]


def test_golden_to_item_metadata_fields(all_goldens):
    for g in all_goldens:
        meta = golden_to_item(g)["metadata"]
        assert "id" in meta, g["id"]
        assert "slice" in meta, g["id"]
        assert meta["id"] == g["id"]
        assert meta["slice"] in ("main", "holdout"), g["id"]


def test_item_id_for_stable(all_goldens):
    for g in all_goldens:
        assert item_id_for(g) == f"qre-golden-{g['id']}"


def test_item_id_for_unique(all_goldens):
    ids = [item_id_for(g) for g in all_goldens]
    assert len(ids) == len(set(ids)), "item ids are not unique"


class _CapturingLangfuse:
    """Fake Langfuse client that records created dataset items (no network)."""

    def __init__(self):
        self.items = []

    def create_dataset(self, **kwargs):
        pass

    def create_dataset_item(self, *, dataset_name, id, input, expected_output, metadata):
        self.items.append({"id": id, "metadata": metadata})


def test_sync_dataset_domain_filter():
    client = _CapturingLangfuse()
    report = sync_dataset(
        dataset_name="ds", domain_filter="development_finance", langfuse=client
    )
    assert report["n"] == len(client.items)
    assert client.items, "expected dev-finance items"
    for it in client.items:
        assert any(
            isinstance(t, dict) and t.get("domain") == "development_finance"
            for t in it["metadata"]["tags"]
        ), it["id"]


def test_sync_dataset_domain_and_slice_filter():
    client = _CapturingLangfuse()
    report = sync_dataset(
        dataset_name="ds",
        domain_filter="development_finance",
        slice_filter="main",
        langfuse=client,
    )
    assert report["n"] == len(client.items)
    assert client.items, "expected dev-finance main items"
    for it in client.items:
        assert it["metadata"]["slice"] == "main", it["id"]
