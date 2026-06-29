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

from dataclasses import dataclass, field
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
from qre.models import in_window

# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


@dataclass
class Facet:
    """One orderedFacet entry from the observation API."""

    earliest_date: str | None
    latest_date: str | None
    obs_count: int
    dates: list[str] = field(default_factory=list)  # per-observation dates; empty when unknown
    provenance_id: str | None = None   # provenanceId from the top-level facets map
    import_name: str | None = None     # importName; dc/base/{importName} is a best-effort fallback


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

    def observation_facets(
        self, *, stat_var: str, entity: str, needs_dates: bool = False
    ) -> list[Facet]:
        """Return orderedFacets for the (stat_var, entity) pair.

        An empty list means no observations. Raises GraphInfraError on transport error.
        needs_dates=False drops the per-observation date/value payload, reducing response
        size. needs_dates=True keeps the full select (current behavior). Facet.dates
        defaults to [] when dates are not requested; summary fields (earliest_date,
        latest_date, obs_count) still populate on the no-date path.
        """
        ...

    def node_labels_batch(self, dcids: list[str]) -> dict[str, str]:
        """Batch-fetch display labels for the given dcids via a single POST /v2/node call.

        Returns {dcid: label} for confirmed reads only. Missing nodes are omitted — never
        fabricated. Raises GraphInfraError on transport or non-2xx.
        Contrast: node_arcs_batch maps absent dcids to None instead of omitting them.
        """
        ...

    def node_arcs_batch(self, dcids: list[str]) -> dict[str, dict | None]:
        """Batch-fetch the full ->* arcs dict for each dcid via a single POST /v2/node.

        Returns {dcid: arcs-or-None} for every requested dcid. A dcid absent from the
        response (or with empty arcs) maps to None — NOT omitted — so the None-on-absent
        invariant matches the single-call node_arcs exactly. Raises GraphInfraError on
        transport or non-2xx.
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
        self._label_cache: dict[str, str] = {}

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
        Repeated calls for the same dcid return the cached label without a
        further HTTP call. None results are not cached (absent nodes may later
        be populated); a GraphInfraError never reaches the cache write.
        """
        if dcid in self._label_cache:
            return self._label_cache[dcid]
        arcs = self._node_data(dcid, "->name")
        if arcs is None:
            return None
        name_nodes = arcs.get("name", {}).get("nodes", [])
        values = [n["value"] for n in name_nodes if n.get("value")]
        if not values:
            return None
        label = _pick_label(values)
        self._label_cache[dcid] = label
        return label

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
            pairs = [
                (sv, sc)
                for sv, sc in zip(raw_svs, raw_scores)
                if sc >= QRE_RELEVANCE_THRESHOLD
            ]
            sv_dcids = [sv for sv, _ in pairs]
            sv_scores = [sc for _, sc in pairs]
        else:
            sv_dcids = raw_svs
            sv_scores = [1.0] * len(raw_svs)

        entity_dcids: list[str] = body.get("entities", [])
        return sv_dcids, entity_dcids, sv_scores

    def observation_facets(
        self, *, stat_var: str, entity: str, needs_dates: bool = False
    ) -> list[Facet]:
        """Return orderedFacets for the (stat_var, entity) pair.

        Uses POST /core/api/v2/observation.
        needs_dates=False → select=["variable","entity","facet"] with no "date" key,
        reducing payload size when per-observation dates are not needed.
        needs_dates=True → full 5-field select with "date": "" (current behavior).
        Facet.dates defaults to [] on the no-date path; summary fields
        (earliest_date, latest_date, obs_count) still populate so coverage_from_facets
        keeps CoverageExact on the no-window path.
        The top-level ``facets`` map (keyed by opaque facetId) carries provenanceId and
        importName; these are mapped onto each returned Facet for provenance resolution.
        The entity param is the observationAbout (donor); the recipient is a constraint
        on the SV, not the entity arg.
        """
        url = f"{self._v2_base}/observation"
        post_body: dict = {
            "select": ["variable", "entity", "facet"],
            "variable": {"dcids": [stat_var]},
            "entity": {"dcids": [entity]},
        }
        if needs_dates:
            post_body["select"] = ["variable", "entity", "date", "value", "facet"]
            post_body["date"] = ""
        body = self._post(url, post_body)
        top_facets_map: dict = body.get("facets", {})
        facets_raw = (
            body.get("byVariable", {})
            .get(stat_var, {})
            .get("byEntity", {})
            .get(entity, {})
            .get("orderedFacets", [])
        )
        result = []
        for f in facets_raw:
            facet_id = f.get("facetId")
            facet_meta = top_facets_map.get(facet_id, {}) if facet_id else {}
            result.append(
                Facet(
                    earliest_date=f.get("earliestDate"),
                    latest_date=f.get("latestDate"),
                    obs_count=f.get("obsCount", 0),
                    dates=[o["date"] for o in f.get("observations", []) if o.get("date")],
                    provenance_id=facet_meta.get("provenanceId"),
                    import_name=facet_meta.get("importName"),
                )
            )
        return result

    def node_labels_batch(self, dcids: list[str]) -> dict[str, str]:
        """Batch-fetch display labels for the given dcids via a single POST /v2/node call.

        Returns {dcid: label} for confirmed reads only. Missing nodes are omitted.
        Raises GraphInfraError on transport or non-2xx.
        Contrast: node_arcs_batch maps absent dcids to None instead of omitting them.
        """
        if not dcids:
            return {}
        url = f"{self._v2_base}/node"
        body = self._post(url, {"nodes": dcids, "property": "->name"})
        data = body.get("data", {})
        result: dict[str, str] = {}
        for dcid in dcids:
            node_data = data.get(dcid)
            if node_data is None:
                continue
            arcs = node_data.get("arcs", {})
            if not arcs:
                continue
            name_nodes = arcs.get("name", {}).get("nodes", [])
            values = [n["value"] for n in name_nodes if n.get("value")]
            if values:
                result[dcid] = _pick_label(values)
        return result

    def node_arcs_batch(self, dcids: list[str]) -> dict[str, dict | None]:
        """Batch-fetch the ->* arcs dict for each dcid via a single POST /v2/node call.

        Returns {dcid: arcs-or-None} for every requested dcid. Absent or empty-arc nodes
        map to None (None-on-absent), matching the single-call node_arcs path.
        Raises GraphInfraError on transport or non-2xx.
        """
        if not dcids:
            return {}
        url = f"{self._v2_base}/node"
        body = self._post(url, {"nodes": dcids, "property": "->*"})
        data = body.get("data", {})
        result: dict[str, dict | None] = {}
        for dcid in dcids:
            node_data = data.get(dcid)
            if node_data is None:
                result[dcid] = None
                continue
            arcs = node_data.get("arcs", {})
            result[dcid] = arcs if arcs else None
        return result

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

        Window-free path sums obs_count; windowed path counts in-window per-facet
        dates using the same expression as coverage_from_facets.
        Returns None when no observations are found (evaluator treats as skip-with-pass).
        Raises GraphInfraError on transport error.
        """
        if not stat_vars or not entities:
            return None
        facets = [
            f
            for sv in stat_vars
            for entity in entities
            for f in self.observation_facets(
                stat_var=sv, entity=entity, needs_dates=(window is not None)
            )
        ]
        if window is None:
            total = sum(f.obs_count for f in facets)
        else:
            total = sum(1 for f in facets for d in f.dates if in_window(d, window))
        return total if total > 0 else None
