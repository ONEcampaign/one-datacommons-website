"""Offline test doubles: FakeLLM and FakeGraph.

Loaded from JSON fixtures in this directory. Used via resolve_async
dependency injection for fully offline testing.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from qre.engine.errors import GraphInfraError
from qre.engine.graph import Facet
from qre.models import in_window

_T = TypeVar("_T", bound=BaseModel)

_FIXTURES_DIR = Path(__file__).parent


def _load_json(name: str) -> dict:
    path = _FIXTURES_DIR / name
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# FakeLLM
# ---------------------------------------------------------------------------


class FakeLLM:
    """Fixture-backed LLM that replays recorded structured outputs.

    Missing fixtures raise KeyError with the schema name, computed key, and the
    full system + user prompt so the missing fixture is authorable without a
    second run.

    Key format: f"{schema_name}:{sha1(system + chr(1) + prompt)}"
    where schema_name = type.__name__ (unqualified class name, per A9).
    """

    def __init__(self, responses: dict | None = None):
        # Accept an explicit dict for per-test injection; fall back to the file.
        self._responses: dict = (
            responses if responses is not None else _load_json("llm_responses.json")
        )

    @staticmethod
    def _key(schema_name: str, system: str, prompt: str) -> str:
        digest = hashlib.sha1((system + "\x01" + prompt).encode()).hexdigest()
        return f"{schema_name}:{digest}"

    def generate_structured(
        self,
        *,
        prompt: str,
        system: str,
        schema: type[_T],
    ) -> _T:
        schema_name = schema.__name__
        key = self._key(schema_name, system, prompt)
        if key not in self._responses:
            raise KeyError(
                f"FakeLLM: missing fixture for schema={schema_name!r}, key={key!r}\n"
                f"  system prompt:\n{system}\n"
                f"  user prompt:\n{prompt}\n"
                f"Add an entry to tests/fixtures/llm_responses.json with the key above."
            )
        return schema.model_validate(self._responses[key])


# ---------------------------------------------------------------------------
# FakeGraph
# ---------------------------------------------------------------------------


class _RaiseOnAny:
    """Sentinel graph that raises GraphInfraError on every call (for error-path tests)."""

    def node_label(self, dcid: str) -> str | None:
        raise GraphInfraError(f"FakeGraph(raise=True): simulated transport error for {dcid!r}")

    def node_arcs(self, dcid: str) -> dict | None:
        raise GraphInfraError(f"FakeGraph(raise=True): simulated transport error for {dcid!r}")

    def node_type(self, dcid: str) -> str | None:
        raise GraphInfraError(f"FakeGraph(raise=True): simulated transport error for {dcid!r}")

    def resolve_entity(self, name: str) -> str | None:
        raise GraphInfraError(f"FakeGraph(raise=True): simulated transport error for {name!r}")

    def detect_svs(self, query: str) -> tuple[list[str], list[str], list[float]]:
        raise GraphInfraError(f"FakeGraph(raise=True): simulated transport error for {query!r}")

    def observation_facets(self, *, stat_var: str, entity: str) -> list[Facet]:
        raise GraphInfraError("FakeGraph(raise=True): simulated transport error")

    def node_labels_batch(self, dcids: list[str]) -> dict[str, str]:
        raise GraphInfraError("FakeGraph(raise=True): simulated transport error")

    def exists(self, dcid: str) -> bool:
        raise GraphInfraError(f"FakeGraph(raise=True): simulated transport error for {dcid!r}")

    def count_observations(self, *, stat_vars, entities, window=None) -> int | None:
        raise GraphInfraError("FakeGraph(raise=True): simulated transport error")


class FakeGraph:
    """Fixture-backed graph client for offline engine tests.

    Implements EngineGraphClient (node_label, node_arcs, node_type, resolve_entity,
    detect_svs, observation_facets) plus the eval-compatible methods (exists,
    count_observations).

    An absent dcid yields node_label=None / exists=False. A missing name arc keeps
    the shape (never drop on missing label).

    Pass raise_on_call=True to get a graph that raises GraphInfraError on every
    call (for testing the error propagation path).
    """

    def __init__(
        self,
        *,
        nodes: dict | None = None,
        obs: dict | None = None,
        detect: dict | None = None,
        resolve: dict | None = None,
        raise_on_call: bool = False,
    ):
        if raise_on_call:
            self._impl = _RaiseOnAny()
            return
        self._impl = None
        self._nodes: dict = nodes if nodes is not None else _load_json("graph_nodes.json")
        self._obs: dict = obs if obs is not None else _load_json("graph_obs.json")
        self._detect: dict = detect if detect is not None else _load_json("graph_detect.json")
        self._resolve: dict = resolve if resolve is not None else _load_json("graph_resolve.json")

    def node_label(self, dcid: str) -> str | None:
        if self._impl is not None:
            return self._impl.node_label(dcid)
        node = self._nodes.get(dcid)
        if node is None:
            return None
        return node.get("label")

    def node_arcs(self, dcid: str) -> dict | None:
        if self._impl is not None:
            return self._impl.node_arcs(dcid)
        node = self._nodes.get(dcid)
        if node is None:
            return None
        arcs = node.get("arcs")
        return arcs if arcs else None

    def node_type(self, dcid: str) -> str | None:
        if self._impl is not None:
            return self._impl.node_type(dcid)
        node = self._nodes.get(dcid)
        if node is None:
            return None
        return node.get("type")

    def resolve_entity(self, name: str) -> str | None:
        if self._impl is not None:
            return self._impl.resolve_entity(name)
        return self._resolve.get(name)

    def detect_svs(self, query: str) -> tuple[list[str], list[str], list[float]]:
        if self._impl is not None:
            return self._impl.detect_svs(query)
        entry = self._detect.get(query, {})
        svs: list[str] = entry.get("svs", [])
        raw_scores: list[float] = entry.get("cosine_scores", [])
        # Mirror LiveGraphClient's threshold filtering: when cosine_scores is
        # present in the fixture (same length as svs), drop SVs below threshold
        # and return the matching filtered scores. Entries without cosine_scores
        # pass through unfiltered with each SV defaulting to score 1.0.
        if raw_scores and len(raw_scores) == len(svs):
            from qre.engine.config import QRE_RELEVANCE_THRESHOLD  # noqa: PLC0415
            filtered = [
                (sv, sc) for sv, sc in zip(svs, raw_scores) if sc >= QRE_RELEVANCE_THRESHOLD
            ]
            if filtered:
                svs, out_scores = zip(*filtered, strict=False)
                svs = list(svs)
                out_scores = list(out_scores)
            else:
                svs = []
                out_scores = []
        else:
            out_scores = [1.0] * len(svs)
        return svs, entry.get("entities", []), out_scores

    def observation_facets(self, *, stat_var: str, entity: str) -> list[Facet]:
        if self._impl is not None:
            return self._impl.observation_facets(stat_var=stat_var, entity=entity)
        key = f"{stat_var}|{entity}"
        raw = self._obs.get(key, [])
        return [
            Facet(
                earliest_date=f.get("earliestDate"),
                latest_date=f.get("latestDate"),
                obs_count=f.get("obsCount", 0),
                dates=f.get(
                    "dates",
                    [o["date"] for o in f.get("observations", []) if o.get("date")],
                ),
                provenance_id=f.get("provenanceId"),
                import_name=f.get("importName"),
            )
            for f in raw
        ]

    def node_labels_batch(self, dcids: list[str]) -> dict[str, str]:
        """Batch-fetch labels from the nodes fixture; absent nodes are omitted."""
        if self._impl is not None:
            return self._impl.node_labels_batch(dcids)
        result: dict[str, str] = {}
        for dcid in dcids:
            node = self._nodes.get(dcid)
            if node is None:
                continue
            label = node.get("label")
            if label:
                result[dcid] = label
        return result

    def exists(self, dcid: str) -> bool:
        if self._impl is not None:
            return self._impl.exists(dcid)
        return dcid in self._nodes

    def count_observations(
        self,
        *,
        stat_vars: list[str],
        entities: list[str],
        window=None,
    ) -> int | None:
        if self._impl is not None:
            return self._impl.count_observations(
                stat_vars=stat_vars, entities=entities, window=window
            )
        facets = [
            f
            for sv in stat_vars
            for entity in entities
            for f in self.observation_facets(stat_var=sv, entity=entity)
        ]
        if window is None:
            total = sum(f.obs_count for f in facets)
        else:
            total = sum(1 for f in facets for d in f.dates if in_window(d, window))
        return total if total > 0 else None
