"""Live fixture recorder for the QRE engine.

Run with live credentials to record fixture files from the staging graph and API.
This is the only step requiring credentials; CI runs entirely offline.

Records LLM responses and graph data with credentials stripped.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

_FIXTURES_DIR = Path(__file__).parent
_GOLDENS_PATH = Path(__file__).parent.parent.parent / "goldens.json"

# Inter-call delay (seconds) between live graph reads during recording. Heavy queries
# (60+ SVs) fire one node read per SV; without a pause they overload the staging node
# endpoint and it returns 503. Tunable via QRE_RECORD_THROTTLE_S. Recording is offline-only
# tooling, so a slower-but-reliable record is the right trade.
_RECORD_THROTTLE_S = float(os.getenv("QRE_RECORD_THROTTLE_S", "0.3"))

_T = TypeVar("_T", bound=BaseModel)

_SECRET_RE = re.compile(
    r"api[_-]?key|authorization|x-goog-api-key|token|credential|secret",
    re.IGNORECASE,
)


def _strip_secrets(obj: Any) -> Any:
    """Strip fields whose names match API key or auth patterns."""
    if isinstance(obj, dict):
        return {
            k: _strip_secrets(v)
            for k, v in obj.items()
            if not _SECRET_RE.search(k)
        }
    if isinstance(obj, list):
        return [_strip_secrets(i) for i in obj]
    return obj


def _llm_key(schema_name: str, system: str, prompt: str) -> str:
    digest = hashlib.sha1((system + "\x01" + prompt).encode()).hexdigest()
    return f"{schema_name}:{digest}"


class _RecordingLLM:
    """Wraps the live LLM and records every call to the fixture dict."""

    def __init__(self, live_llm, store: dict):
        self._llm = live_llm
        self._store = store

    def generate_structured(self, *, prompt: str, system: str, schema: type[_T]) -> _T:
        result = self._llm.generate_structured(prompt=prompt, system=system, schema=schema)
        key = _llm_key(schema.__name__, system, prompt)
        self._store[key] = result.model_dump(mode="json")
        return result


class _RecordingGraph:
    """Wraps the live graph client and records every call to the fixture dicts."""

    def __init__(self, live_graph, nodes: dict, obs: dict, detect: dict, resolve: dict):
        self._graph = live_graph
        self._nodes = nodes
        self._obs = obs
        self._detect = detect
        self._resolve = resolve

    def node_label(self, dcid: str) -> str | None:
        result = self._graph.node_label(dcid)
        self._record_node(dcid)
        return result

    def node_arcs(self, dcid: str) -> dict | None:
        time.sleep(_RECORD_THROTTLE_S)
        result = self._graph.node_arcs(dcid)
        if result is not None:
            label = self._graph.node_label(dcid)
            node_type = self._graph.node_type(dcid)
            self._nodes[dcid] = {
                "label": label,
                "type": node_type,
                "arcs": _strip_secrets(result),
            }
        return result

    def node_type(self, dcid: str) -> str | None:
        return self._graph.node_type(dcid)

    def _record_node(self, dcid: str) -> None:
        if dcid not in self._nodes:
            time.sleep(_RECORD_THROTTLE_S)
            arcs = self._graph.node_arcs(dcid)
            if arcs is not None:
                label = self._graph.node_label(dcid)
                node_type = self._graph.node_type(dcid)
                self._nodes[dcid] = {
                    "label": label,
                    "type": node_type,
                    "arcs": _strip_secrets(arcs),
                }

    def resolve_entity(self, name: str) -> str | None:
        time.sleep(_RECORD_THROTTLE_S)
        result = self._graph.resolve_entity(name)
        self._resolve[name] = result
        return result

    def detect_svs(self, query: str) -> tuple[list[str], list[str], list[float]]:
        time.sleep(_RECORD_THROTTLE_S)
        svs, entities, scores = self._graph.detect_svs(query)
        self._detect[query] = {"svs": svs, "entities": entities, "cosine_scores": scores}
        return svs, entities, scores

    def observation_facets(self, *, stat_var: str, entity: str, needs_dates: bool = False):
        time.sleep(_RECORD_THROTTLE_S)
        # Recording stores only summary fields (obs_count/earliest_date/latest_date);
        # per-observation dates are never stored in the fixture format.
        # needs_dates is accepted for Protocol conformance but not threaded further.
        result = self._graph.observation_facets(stat_var=stat_var, entity=entity)
        key = f"{stat_var}|{entity}"
        self._obs[key] = [
            {
                "earliestDate": f.earliest_date,
                "latestDate": f.latest_date,
                "obsCount": f.obs_count,
            }
            for f in result
        ]
        return result

    def node_arcs_batch(self, dcids: list[str]) -> dict[str, dict | None]:
        time.sleep(_RECORD_THROTTLE_S)
        result = self._graph.node_arcs_batch(dcids)
        for dcid, arcs in result.items():
            if arcs is not None:
                node = self._nodes.setdefault(dcid, {"label": None, "type": None})
                node["arcs"] = _strip_secrets(arcs)
        return result

    def node_labels_batch(self, dcids: list[str]) -> dict[str, str]:
        time.sleep(_RECORD_THROTTLE_S)
        result = self._graph.node_labels_batch(dcids)
        for dcid, label in result.items():
            node = self._nodes.setdefault(dcid, {"label": None, "type": None})
            node["label"] = label
        return result

    def exists(self, dcid: str) -> bool:
        return self._graph.exists(dcid)

    def count_observations(self, *, stat_vars, entities, window=None) -> int | None:
        return self._graph.count_observations(stat_vars=stat_vars, entities=entities, window=window)


def _write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)
    print(f"  Wrote {path}")


def main() -> None:
    import argparse

    from qre.engine.graph import LiveGraphClient
    from qre.engine.llm import LLM
    from qre.models import RawTextInput, ResolveRequest

    parser = argparse.ArgumentParser(description="QRE fixture recorder")
    parser.add_argument(
        "--domain",
        default="development_finance",
        help=(
            "Domain tag to filter goldens for recording "
            "(default: development_finance; use 'standard' for standard-DC goldens)."
        ),
    )
    parser.add_argument(
        "--ids",
        default=None,
        help="Comma-separated golden ids to record (intersected with --domain).",
    )
    args = parser.parse_args()
    domain = args.domain
    only_ids = {i.strip() for i in args.ids.split(",")} if args.ids else None

    print(f"QRE fixture recorder (domain={domain!r}; requires GEMINI_API_KEY and graph access)")

    goldens = json.loads(_GOLDENS_PATH.read_text())
    domain_goldens = [
        g for g in goldens
        if any(t.get("domain") == domain for t in g.get("tags", []))
        and (only_ids is None or g.get("id") in only_ids)
    ]
    if only_ids:
        print(f"Recording only ids: {sorted(only_ids)}")
    print(f"Found {len(domain_goldens)} {domain!r} goldens to record")

    # Load existing fixtures so new entries are merged (upserted) rather than replacing.
    def _load_existing(name: str) -> dict:
        path = _FIXTURES_DIR / name
        if path.exists():
            with open(path) as _f:
                return json.load(_f)
        return {}

    llm_store: dict = _load_existing("llm_responses.json")
    nodes_store: dict = _load_existing("graph_nodes.json")
    obs_store: dict = _load_existing("graph_obs.json")
    detect_store: dict = _load_existing("graph_detect.json")
    resolve_store: dict = _load_existing("graph_resolve.json")

    live_graph = LiveGraphClient()
    live_llm = LLM()
    rec_graph = _RecordingGraph(live_graph, nodes_store, obs_store, detect_store, resolve_store)
    rec_llm = _RecordingLLM(live_llm, llm_store)

    try:
        import asyncio

        from qre.engine.core import resolve_async

        for golden in domain_goldens:
            gid = golden["id"]
            query = golden["query"]
            print(f"  Recording {gid}: {query!r}")
            _max_attempts = 5
            for _attempt in range(1, _max_attempts + 1):
                try:
                    request = ResolveRequest(input=RawTextInput(query=query))
                    asyncio.run(resolve_async(request, graph=rec_graph, llm=rec_llm))
                    break
                except Exception as exc:
                    if _attempt < _max_attempts:
                        _wait = 2 ** _attempt
                        print(f"    attempt {_attempt} FAIL ({exc}); retrying in {_wait}s")
                        time.sleep(_wait)
                    else:
                        print(f"    ERROR on {gid} (all {_max_attempts} attempts): {exc}")
    except ImportError:
        print("  qre.engine.core not yet available; recording graph probes only.")
        # Record graph facts for known dcids from the fixture files even without the full engine.
        known_dcids = [
            "DevelopmentFinance", "DevelopmentFinanceFlow", "measuredValue",
            "DevelopmentFinanceScheme", "DevelopmentFinancePurpose", "DevelopmentFinanceRecipient",
            "ODAGrants", "OfficialDevelopmentAssistance",
            "DAC/Health", "DAC/BasicHealth", "DAC/STDcontrolincludingHIVAIDS",
            "DAC/Reproductivehealthcare", "DAC/Healtheducation", "DAC/Medicaleducationtraining",
            "country/ETH", "country/KEN", "country/USA", "country/GBR",
            "country/DEU", "country/FRA", "country/IND",
            "ONE/CRS_DAC/Health-ODAGrants-ETH",
            "ONE/CRS_DAC/Health-ODAGrants-KEN",
            "ONE/CRS_DAC/Health-OfficialDevelopmentAssistance-ETH",
            "ONE/CRS_DAC/STDcontrolincludingHIVAIDS-ODAGrants-KEN",
            "ONE/CRS_DAC/BasicHealth-ODAGrants-KEN",
            "ONE/CRS_DAC/Reproductivehealthcare-ODAGrants-ETH",
        ]
        for dcid in known_dcids:
            print(f"  Probing node: {dcid}")
            try:
                rec_graph.node_arcs(dcid)
            except Exception as exc:
                print(f"    ERROR: {exc}")

    print("\nWriting fixture files...")
    _write_json(_FIXTURES_DIR / "llm_responses.json", llm_store)
    _write_json(_FIXTURES_DIR / "graph_nodes.json", nodes_store)
    _write_json(_FIXTURES_DIR / "graph_obs.json", obs_store)
    _write_json(_FIXTURES_DIR / "graph_detect.json", detect_store)
    _write_json(_FIXTURES_DIR / "graph_resolve.json", resolve_store)

    print("\nDone. Run 'uv run pytest -q tests/fixtures tests/engine' to verify.")


if __name__ == "__main__":
    main()
