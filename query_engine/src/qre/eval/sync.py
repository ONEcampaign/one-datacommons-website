"""CLI entrypoint: python -m qre.eval.sync

Pushes goldens.json to a Langfuse dataset. Requires LANGFUSE_PUBLIC_KEY and
LANGFUSE_SECRET_KEY in the environment.

Examples::

    python -m qre.eval.sync
    python -m qre.eval.sync --dataset-name qre-goldens-v1 --slice main
"""
import argparse

from qre.eval.dataset import sync_dataset


def _main() -> None:
    parser = argparse.ArgumentParser(description="Sync QRE goldens to a Langfuse dataset.")
    parser.add_argument(
        "--dataset-name",
        default="qre-goldens-v1",
        help="Langfuse dataset name (default: qre-goldens-v1)",
    )
    parser.add_argument(
        "--goldens-path",
        default=None,
        help="Path to goldens.json (default: package-relative location)",
    )
    parser.add_argument(
        "--slice",
        dest="slice_filter",
        default=None,
        choices=["main", "holdout"],
        help="Sync only goldens with this slice value",
    )
    parser.add_argument(
        "--domain",
        dest="domain_filter",
        default=None,
        help='Sync only goldens tagged with this domain (e.g. "development_finance")',
    )
    parser.add_argument(
        "--gate-only",
        dest="gate_only",
        action="store_true",
        help="Keep only gate-counting statuses (drops DEFERRED/UNVERIFIED); for merge gates",
    )
    args = parser.parse_args()
    report = sync_dataset(
        dataset_name=args.dataset_name,
        goldens_path=args.goldens_path,
        slice_filter=args.slice_filter,
        domain_filter=args.domain_filter,
        gate_only=args.gate_only,
    )
    archived = report.get("archived") or []
    print(
        f"Synced {report['n']} items to '{report['dataset']}' "
        f"(holdout in batch: {report['holdout']}; archived stale: {len(archived)})"
    )
    if archived:
        print("  archived: " + ", ".join(archived))


if __name__ == "__main__":
    _main()
