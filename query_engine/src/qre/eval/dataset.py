"""Golden corpus sync: maps goldens.json records to Langfuse dataset items and pushes them."""
import json
import os
from pathlib import Path
from typing import Any


def golden_to_item(golden: dict) -> dict:
    """Map one goldens.json record to a Langfuse dataset item dict (pure, no network).

    Returns a dict with keys ``input``, ``expected_output``, and ``metadata``.
    """
    return {
        "input": {
            "query": golden["query"],
            "entry_path": golden["entry_path"],
        },
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


def item_id_for(golden: dict) -> str:
    """Return a stable, human-readable upsert key for a golden record."""
    return f"qre-golden-{golden['id']}"


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


def sync_dataset(
    *,
    dataset_name: str = "qre-goldens-v1",
    goldens_path: str | Path | None = None,
    slice_filter: str | None = None,
    langfuse: Any = None,
) -> dict:
    """Push goldens.json to a Langfuse dataset, upserting by stable item id.

    Args:
        dataset_name: Name of the Langfuse dataset to create or update.
        goldens_path: Path to goldens.json; defaults to the package-relative location.
        slice_filter: When set, only sync goldens where golden["slice"] == slice_filter.
        langfuse: Optional pre-built Langfuse client (for testing or DI).

    Returns a dict {dataset, n, holdout} with the count of items synced.
    The id field uses upsert semantics: duplicate ids overwrite previous items.
    """
    client = langfuse or _client()
    all_goldens = _load_goldens(goldens_path)
    goldens = (
        [g for g in all_goldens if g["slice"] == slice_filter]
        if slice_filter is not None
        else all_goldens
    )

    client.create_dataset(
        name=dataset_name,
        description="QRE Phase 0 golden corpus",
        metadata={"source": "goldens.json", "slice_filter": slice_filter},
    )
    for g in goldens:
        item = golden_to_item(g)
        client.create_dataset_item(
            dataset_name=dataset_name,
            id=item_id_for(g),
            input=item["input"],
            expected_output=item["expected_output"],
            metadata=item["metadata"],
        )

    holdout_count = sum(1 for g in goldens if g["slice"] == "holdout")
    return {"dataset": dataset_name, "n": len(goldens), "holdout": holdout_count}
