"""Golden corpus sync: maps goldens.json records to Langfuse dataset items and pushes them."""
import json
import os
from pathlib import Path
from typing import Any


def golden_to_item(golden: dict) -> dict:
    """Map one goldens.json record to a Langfuse dataset item dict (pure, no network).

    Returns a dict with keys ``input``, ``expected_output``, and ``metadata``.
    For spec_resubmit goldens, ``input`` also carries the required shape_id/slots
    so the runner's spec_resubmit branch can build a ResolveRequest without KeyError.
    """
    inp: dict = {"query": golden["query"], "entry_path": golden["entry_path"]}
    if golden["entry_path"] == "spec_resubmit":
        inp["shape_id"] = golden["shape_id"]
        inp["slots"] = golden["slots"]
        inp["stat_var_dcids"] = golden.get("stat_var_dcids")
        inp["entity_dcids"] = golden.get("entity_dcids")
    return {
        "input": inp,
        "expected_output": {
            "expected_status": golden["expected_status"],
            "expected_shape": golden["expected_shape"],
            "expected_slots": golden["expected_slots"],
            "expected_stat_vars": golden["expected_stat_vars"],
            "expected_entities": golden["expected_entities"],
            "expected_no_data_reason": golden["expected_no_data_reason"],
            "candidate_count": golden["candidate_count"],
        },
        "metadata": {
            "id": golden["id"],
            "slice": golden["slice"],
            "tags": golden["tags"],
            "status": golden["status"],
            "notes": golden["notes"],
        },
    }


def item_id_for(golden: dict, dataset_name: str | None = None) -> str:
    """Return a stable, human-readable upsert key for a golden record.

    Langfuse dataset-item ids are globally unique and cannot be reused across datasets,
    so the id is scoped by dataset_name when given (the same golden can then appear in
    both the full-corpus and a domain-scoped dataset).
    """
    base = f"qre-golden-{golden['id']}"
    return f"{dataset_name}:{base}" if dataset_name else base


def _load_goldens(path: str | Path | None = None) -> list[dict]:
    """Load goldens.json from the given path or the default location.

    The default resolves to ``query_engine/goldens.json`` relative to this file,
    but can be overridden via ``QRE_GOLDENS_PATH``. Reads only when called.
    """
    if path is None:
        env_path = os.environ.get("QRE_GOLDENS_PATH")
        if env_path:
            path = Path(env_path)
        else:
            # This file lives at src/qre/eval/dataset.py; goldens.json is three levels up.
            path = Path(__file__).resolve().parents[3] / "goldens.json"
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _client():
    """Build and return a Langfuse client, raising clearly if credentials are unset."""
    from langfuse import get_client

    missing = [k for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            f"Langfuse credentials missing: {', '.join(missing)}. Set them in your shell "
            "or .env (LANGFUSE_BASE_URL defaults to https://cloud.langfuse.com). "
            "Keys are in Langfuse UI -> Settings -> API Keys."
        )
    return get_client()


def _has_domain(golden: dict, domain: str) -> bool:
    """Return True if the golden carries a {"domain": <domain>} tag."""
    return any(
        isinstance(t, dict) and t.get("domain") == domain for t in golden.get("tags", [])
    )


# Statuses the merge gate counts (eval-gate.md section 2). DEFERRED / UNVERIFIED /
# HOLDOUT goldens are excluded from a gate-only dataset so a known-unresolvable or
# unreviewed golden never drags a gated metric. cand-r2 (sub-national geo gap) is the
# first such exclusion.
_GATE_STATUSES = frozenset({"VERIFIED_AGAINST_DATA", "VERIFIED_AGAINST_GRAPH"})


def sync_dataset(
    *,
    dataset_name: str = "qre-goldens-v1",
    goldens_path: str | Path | None = None,
    slice_filter: str | None = None,
    domain_filter: str | None = None,
    gate_only: bool = False,
    langfuse: Any = None,
) -> dict:
    """Push goldens.json to a Langfuse dataset, upserting by stable item id.

    Args:
        dataset_name: Name of the Langfuse dataset to create or update.
        goldens_path: Path to goldens.json; defaults to the package-relative location.
        slice_filter: When set, only sync goldens where golden["slice"] == slice_filter.
        domain_filter: When set, only sync goldens tagged {"domain": domain_filter}.
        gate_only: When True, keep only goldens whose status is gate-counting
            (VERIFIED_AGAINST_DATA / VERIFIED_AGAINST_GRAPH), dropping DEFERRED /
            UNVERIFIED / HOLDOUT-status items. Use for merge-gate datasets.
        langfuse: Optional pre-built Langfuse client (for testing or DI).

    Returns a dict {dataset, n, holdout, archived} with the counts synced.
    The id field uses upsert semantics: duplicate ids overwrite previous items.
    The dataset is reconciled to the batch: any pre-existing ACTIVE item whose id
    is not in the batch is archived (Langfuse has no hard delete), so a filtered
    sync cannot leave a stale item behind to be scored. Without this, a previously
    synced item (e.g. a golden later marked DEFERRED) would silently drag the gate.
    """
    client = langfuse or _client()
    goldens = _load_goldens(goldens_path)
    if slice_filter is not None:
        goldens = [g for g in goldens if g["slice"] == slice_filter]
    if domain_filter is not None:
        goldens = [g for g in goldens if _has_domain(g, domain_filter)]
    if gate_only:
        goldens = [g for g in goldens if g.get("status") in _GATE_STATUSES]

    client.create_dataset(
        name=dataset_name,
        description="QRE Phase 0 golden corpus",
        metadata={"source": "goldens.json", "slice_filter": slice_filter},
    )
    batch_ids = set()
    for g in goldens:
        item = golden_to_item(g)
        item_id = item_id_for(g, dataset_name)
        batch_ids.add(item_id)
        client.create_dataset_item(
            dataset_name=dataset_name,
            id=item_id,
            input=item["input"],
            expected_output=item["expected_output"],
            metadata=item["metadata"],
        )

    archived = _archive_stale(client, dataset_name, batch_ids)

    holdout_count = sum(1 for g in goldens if g["slice"] == "holdout")
    return {
        "dataset": dataset_name,
        "n": len(goldens),
        "holdout": holdout_count,
        "archived": archived,
    }


def _archive_stale(client: Any, dataset_name: str, batch_ids: set[str]) -> list[str]:
    """Archive ACTIVE items in the dataset whose id is not in batch_ids; return their ids.

    create_dataset() runs just before this, so the dataset always exists -- a real
    transport error from get_dataset must propagate (fail loud), not silently skip
    reconciliation and leave a stale item to be scored. Only a client that does not
    implement get_dataset (a minimal test double) has nothing to reconcile.
    """
    try:
        existing = client.get_dataset(dataset_name).items
    except AttributeError:
        return []
    archived = []
    for it in existing:
        status = getattr(it, "status", None)
        status = getattr(status, "value", status)
        if it.id not in batch_ids and status == "ACTIVE":
            client.create_dataset_item(dataset_name=dataset_name, id=it.id, status="ARCHIVED")
            archived.append(it.id)
    return archived
