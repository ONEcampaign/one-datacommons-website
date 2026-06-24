"""Graph-client seam for the QRE engine.

Defines:
* Facet: observation facet data returned by the graph.
* EngineGraphClient: Protocol for the engine-internal methods (node_label, node_arcs,
  node_type, resolve_entity, detect_svs, observation_facets). Engine stages only import
  this Protocol; they do NOT import qre.eval.
* LiveGraphClient: httpx implementation. Config-driven target (QRE_GRAPH_BASE); the
  staging-vs-prod distinction is a config value, not a class name.

LiveGraphClient also duck-types qre.eval.graph.GraphClient (it exposes exists and
count_observations) so it can be passed to run_eval without changes to the eval harness.

Every method raises GraphInfraError on:
  - transport error (httpx.RequestError)
  - non-2xx response
  - non-JSON response body
  - timeout

node_label and node_arcs return None ONLY on a genuine 200 with an absent/empty node.
Returning a falsy "not found" value on a transport failure would allow fabrication or
false no_data to slip through undetected.

DO NOT import qre.eval from this module (isolation invariant).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from urllib.parse import urlencode

import httpx

from qre.engine.config import (
    BROWSER_UA,
    QRE_GRAPH_BASE,
    QRE_GRAPH_TIMEOUT_S,
    QRE_RELEVANCE_THRESHOLD,
)
from qre.engine.errors import GraphInfraError

# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


@dataclass
class Facet:
    """One orderedFacet entry from the observation API."""

    earliest_date: str | None
    latest_date: str | None
    obs_count: int


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class EngineGraphClient(Protocol):
    """Graph-access interface used by the engine pipeline stages.

    This Protocol covers only the engine-internal methods (node_label, node_arcs,
    node_type, resolve_entity, detect_svs, observation_facets). The methods
    exists() and count_observations() are NOT part of this Protocol; LiveGraphClient
    exposes them separately to duck-type qre.eval.graph.GraphClient so a single
    client instance can be passed to both the engine pipeline and run_eval.
    """

    def node_label(self, dcid: str) -> str | None:
        """Return the display label for a node, or None if absent.

        Label selection: take the LAST name value (the fuller rollup label),
        falling back to the first when only one value is returned.
        Returns None only on a genuine 200 with an absent/empty node.
        Raises GraphInfraError on transport or non-2xx.
        """
        ...

    def node_arcs(self, dcid: str) -> dict | None:
        """Return the full ->* arcs dict for a node, or None if absent.

        Returns None only on a genuine 200 with an absent/empty node.
        Raises GraphInfraError on transport or non-2xx.
        """
        ...

    def node_type(self, dcid: str) -> str | None:
        """Return the first typeOf dcid for a node, or None if absent."""
        ...

    def resolve_entity(self, name: str) -> str | None:
        """Resolve an entity name to its dcid (Country-typed), or None if unresolved."""
        ...

    def detect_svs(self, query: str) -> tuple[list[str], list[str], list[float]]:
        """Return (candidate_sv_dcids, entity_dcids, candidate_sv_scores).

        candidate_sv_scores[i] is the cosine score of candidate_sv_dcids[i],
        post-threshold, in the same order. Scores appended last (not merged into
        tuples) so existing 2-tuple unpacks (`svs, entities = ...`) become a natural
        extension (`svs, entities, scores = ...`). Recall aid only.
        """
        ...

    def observation_facets(self, *, stat_var: str, entity: str) -> list[Facet]:
        """Return orderedFacets for the (stat_var, entity) pair.

        An empty list means no observations. Raises GraphInfraError on transport error.
        """
        ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pick_label(values: list[str]) -> str:
    """Deterministic label selection: last value (fuller rollup), fallback to first."""
    return values[-1]


# ---------------------------------------------------------------------------
# Live implementation
# ---------------------------------------------------------------------------


class LiveGraphClient:
    """httpx-backed implementation of EngineGraphClient.

    Sync. The async engine core calls graph methods via asyncio.to_thread.
    One httpx.Client per instance; pass the same instance across the pipeline
    to share the connection pool.

    Also satisfies qre.eval.graph.GraphClient (structural duck-typing): exposes
    exists() and count_observations(), so this client can be passed to run_eval
    without modifying the eval harness.
    """

    def __init__(
        self,
        *,
        base: str | None = None,
        timeout: float | None = None,
    ):
        _base = (base or QRE_GRAPH_BASE).rstrip("/")
        _timeout = timeout if timeout is not None else QRE_GRAPH_TIMEOUT_S

        self._v2_base = f"{_base}/core/api/v2"
        self._detect_url = f"{_base}/api/explore/detect"
        self._headers = {
            "User-Agent": BROWSER_UA,
            "Content-Type": "application/json",
        }
        self._client = httpx.Client(timeout=_timeout)

    def _post(self, url: str, payload: dict) -> dict:
        """POST JSON payload and return parsed JSON body.

        Raises GraphInfraError on transport error, non-2xx, or non-JSON body.
        """
        try:
            resp = self._client.post(url, json=payload, headers=self._headers)
        except httpx.RequestError as exc:
            raise GraphInfraError(f"Graph transport error: {exc}") from exc
        if resp.status_code != 200:
            raise GraphInfraError(
                f"Graph returned HTTP {resp.status_code} for {url!r}"
            )
        try:
            return resp.json()
        except Exception as exc:
            raise GraphInfraError(f"Graph returned non-JSON body for {url!r}: {exc}") from exc

    def _node_data(self, dcid: str, prop: str) -> dict | None:
        """POST /core/api/v2/node for a single dcid + property.

        Returns the arcs dict for that dcid, or None if the node is absent.
        """
        url = f"{self._v2_base}/node"
        body = self._post(url, {"nodes": [dcid], "property": prop})
        data = body.get("data", {})
        node_data = data.get(dcid)
        if node_data is None:
            return None
        arcs = node_data.get("arcs", {})
        return arcs if arcs else None

    # -----------------------------------------------------------------------
    # EngineGraphClient methods
    # -----------------------------------------------------------------------

    def node_label(self, dcid: str) -> str | None:
        """Return the display label for a node using the ->name arc.

        Deterministic: take the LAST name value (the fuller rollup label),
        falling back to the first when only one value is returned.
        Returns None on genuine 200 with absent/empty node.
        """
        arcs = self._node_data(dcid, "->name")
        if arcs is None:
            return None
        name_nodes = arcs.get("name", {}).get("nodes", [])
        values = [n["value"] for n in name_nodes if n.get("value")]
        if not values:
            return None
        return _pick_label(values)

    def node_arcs(self, dcid: str) -> dict | None:
        """Return all ->* arcs for a node, or None if absent."""
        return self._node_data(dcid, "->*")

    def node_type(self, dcid: str) -> str | None:
        """Return the first typeOf dcid for a node, or None."""
        arcs = self._node_data(dcid, "->typeOf")
        if arcs is None:
            return None
        typeof_nodes = arcs.get("typeOf", {}).get("nodes", [])
        if not typeof_nodes:
            return None
        return typeof_nodes[0].get("dcid")

    def resolve_entity(self, name: str) -> str | None:
        """Resolve an entity name to its dcid (Country-typed).

        Uses the /core/api/v2/resolve endpoint with the Country typeOf filter.
        Returns the first candidate dcid, or None if unresolved.
        """
        url = f"{self._v2_base}/resolve"
        body = self._post(url, {
            "nodes": [name],
            "property": "<-description{typeOf:Country}->dcid",
        })
        entities = body.get("entities", [])
        if not entities:
            return None
        candidates = entities[0].get("candidates", [])
        if not candidates:
            return None
        return candidates[0].get("dcid")

    def detect_svs(self, query: str) -> tuple[list[str], list[str], list[float]]:
        """Return (candidate_sv_dcids, entity_dcids, candidate_sv_scores).

        Uses POST with contextHistory=[]. The debug.sv_matching.SV field returns
        candidates paired with CosineScore relevance scores. SVs below
        QRE_RELEVANCE_THRESHOLD are dropped before returning so that genuinely
        unknown variables surface as an empty list rather than low-confidence noise.
        Entities are returned as-is. When scores are absent or mismatched (legacy
        branch), every returned SV gets score 1.0 (length-matched).
        """
        url = f"{self._detect_url}?{urlencode({'q': query})}"
        try:
            resp = self._client.post(
                url,
                json={"contextHistory": []},
                headers=self._headers,
            )
        except httpx.RequestError as exc:
            raise GraphInfraError(f"Graph transport error (detect): {exc}") from exc
        if resp.status_code != 200:
            raise GraphInfraError(f"Graph detect returned HTTP {resp.status_code}")
        try:
            body = resp.json()
        except Exception as exc:
            raise GraphInfraError(f"Graph detect returned non-JSON body: {exc}") from exc

        sv_matching = body.get("debug", {}).get("sv_matching", {})
        raw_svs: list[str] = sv_matching.get("SV", [])
        raw_scores: list[float] = sv_matching.get("CosineScore", [])

        # Apply relevance threshold: keep SVs at or above the threshold with their
        # scores; when scores are absent or mismatched, assign 1.0 to each SV.
        if raw_scores and len(raw_scores) == len(raw_svs):
            sv_dcids, sv_scores = zip(
                *[(sv, sc) for sv, sc in zip(raw_svs, raw_scores) if sc >= QRE_RELEVANCE_THRESHOLD],
                strict=False,
            ) if any(sc >= QRE_RELEVANCE_THRESHOLD for sc in raw_scores) else ([], [])
            sv_dcids = list(sv_dcids)
            sv_scores = list(sv_scores)
        else:
            sv_dcids = raw_svs
            sv_scores = [1.0] * len(raw_svs)

        entity_dcids: list[str] = body.get("entities", [])
        return sv_dcids, entity_dcids, sv_scores

    def observation_facets(self, *, stat_var: str, entity: str) -> list[Facet]:
        """Return orderedFacets for the (stat_var, entity) pair.

        Uses POST /core/api/v2/observation with select=[variable,entity,date,value].
        The entity param is the observationAbout (donor); the recipient is a constraint
        on the SV, not the entity arg.
        """
        url = f"{self._v2_base}/observation"
        body = self._post(url, {
            "select": ["variable", "entity", "date", "value"],
            "variable": {"dcids": [stat_var]},
            "entity": {"dcids": [entity]},
            "date": "",
        })
        facets_raw = (
            body.get("byVariable", {})
            .get(stat_var, {})
            .get("byEntity", {})
            .get(entity, {})
            .get("orderedFacets", [])
        )
        return [
            Facet(
                earliest_date=f.get("earliestDate"),
                latest_date=f.get("latestDate"),
                obs_count=f.get("obsCount", 0),
            )
            for f in facets_raw
        ]

    def close(self) -> None:
        """Close the underlying httpx client. Safe to call more than once."""
        self._client.close()

    # -----------------------------------------------------------------------
    # Eval-compatible methods (duck-types qre.eval.graph.GraphClient)
    # -----------------------------------------------------------------------

    def exists(self, dcid: str) -> bool:
        """Return True if the node has any arcs. Raises GraphInfraError on transport error."""
        return self.node_arcs(dcid) is not None

    def count_observations(
        self,
        *,
        stat_vars: list[str],
        entities: list[str],
        window=None,
    ) -> int | None:
        """Count distinct (date, facetId) pairs across stat_vars × entities.

        Returns None when no observations are found (evaluator treats as skip-with-pass).
        Raises GraphInfraError on transport error.
        """
        if not stat_vars or not entities:
            return None
        total = sum(
            f.obs_count
            for sv in stat_vars
            for entity in entities
            for f in self.observation_facets(stat_var=sv, entity=entity)
        )
        return total if total > 0 else None
