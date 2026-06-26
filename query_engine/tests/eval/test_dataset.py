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


def test_sync_dataset_gate_only_excludes_deferred():
    """gate_only drops non-gate statuses (e.g. cand-r2, DEFERRED) from the merge set."""
    ids_with = {
        it["id"]
        for it in _sync_ids(domain_filter="standard", slice_filter="main", gate_only=False)
    }
    ids_gate = {
        it["id"]
        for it in _sync_ids(domain_filter="standard", slice_filter="main", gate_only=True)
    }
    cand_r2 = "ds:qre-golden-cand-r2"
    assert cand_r2 in ids_with, "cand-r2 should be present without gate_only"
    assert cand_r2 not in ids_gate, "cand-r2 (DEFERRED) must be excluded by gate_only"
    assert ids_gate < ids_with


def _sync_ids(**kwargs):
    client = _CapturingLangfuse()
    sync_dataset(dataset_name="ds", langfuse=client, **kwargs)
    return client.items


class _ReconcilingLangfuse:
    """Fake Langfuse that persists items by id and supports get_dataset for reconciliation."""

    class _Item:
        def __init__(self, id, status="ACTIVE"):
            self.id = id
            self.status = status

    def __init__(self, preexisting_ids=()):
        # Map id -> status. Seed with stale items already in the dataset.
        self._items = {i: "ACTIVE" for i in preexisting_ids}

    def create_dataset(self, **kwargs):
        pass

    def create_dataset_item(self, *, dataset_name, id, status=None, **kwargs):
        self._items[id] = "ARCHIVED" if str(status).endswith("ARCHIVED") else "ACTIVE"

    def get_dataset(self, name):
        items = [self._Item(i, s) for i, s in self._items.items()]
        return type("_DS", (), {"items": items})()

    def active_ids(self):
        return {i for i, s in self._items.items() if s == "ACTIVE"}


def test_sync_dataset_archives_stale_items():
    """A previously-synced item not in the new batch is archived, not left to be scored."""
    stale = "qre-standard-main:qre-golden-cand-r2"
    client = _ReconcilingLangfuse(preexisting_ids=[stale])
    report = sync_dataset(
        dataset_name="qre-standard-main",
        domain_filter="standard",
        slice_filter="main",
        gate_only=True,
        langfuse=client,
    )
    assert stale in report["archived"], "stale cand-r2 should be archived"
    assert stale not in client.active_ids(), "cand-r2 must not remain ACTIVE after reconcile"
    # The 10 gate items are all ACTIVE; the stale one is gone.
    assert len(client.active_ids()) == report["n"] == 10
