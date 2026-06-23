"""Live fixture recorder for the QRE engine.

Run with live credentials to record fixture files from the staging graph and API.
This is the only step requiring credentials; CI runs entirely offline.

Records LLM responses and graph data with credentials stripped.
LLM responses are keyed by schema_name:sha1(system+chr(1)+prompt).
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

_FIXTURES_DIR = Path(__file__).parent
_GOLDENS_PATH = Path(__file__).parent.parent.parent / "goldens.json"

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
        result = self._graph.resolve_entity(name)
        self._resolve[name] = result
        return result

    def detect_svs(self, query: str) -> tuple[list[str], list[str]]:
        svs, entities = self._graph.detect_svs(query)
        self._detect[query] = {"svs": svs, "entities": entities}
        return svs, entities

    def observation_facets(self, *, stat_var: str, entity: str):
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
    from qre.engine.graph import LiveGraphClient
    from qre.engine.llm import LLM
    from qre.models import RawTextInput, ResolveRequest

    print("QRE fixture recorder (requires GEMINI_API_KEY and graph access)")

    goldens = json.loads(_GOLDENS_PATH.read_text())
    df_goldens = [
        g for g in goldens
        if any(t.get("domain") == "development_finance" for t in g.get("tags", []))
    ]
    print(f"Found {len(df_goldens)} dev-finance goldens to record")

    llm_store: dict = {}
    nodes_store: dict = {}
    obs_store: dict = {}
    detect_store: dict = {}
    resolve_store: dict = {}

    live_graph = LiveGraphClient()
    live_llm = LLM()
    rec_graph = _RecordingGraph(live_graph, nodes_store, obs_store, detect_store, resolve_store)
    rec_llm = _RecordingLLM(live_llm, llm_store)

    # Import resolve_async lazily so record.py can be imported from Slice A.
    try:
        import asyncio

        from qre.engine.core import resolve_async

        for golden in df_goldens:
            gid = golden["id"]
            query = golden["query"]
            print(f"  Recording {gid}: {query!r}")
            try:
                request = ResolveRequest(input=RawTextInput(query=query))
                asyncio.run(resolve_async(request, graph=rec_graph, llm=rec_llm))
            except Exception as exc:
                print(f"    ERROR on {gid}: {exc}")
    except ImportError:
        print("  qre.engine.core not yet available (Slice D); recording graph probes only.")
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
