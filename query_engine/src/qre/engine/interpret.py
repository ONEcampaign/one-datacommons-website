"""Recall stage: graph-backed SV detection and entity resolution.

Calls graph.detect_svs() for candidate SV dcids and graph.resolve_entity() for
each entity name. Entities that fail to resolve are silently dropped.

All graph calls run in asyncio.to_thread (graph client is sync).
"""
from __future__ import annotations

import asyncio

from pydantic import BaseModel

from qre.engine.graph import EngineGraphClient


class Recall(BaseModel):
    """Graph-backed recall: candidate SVs, their cosine scores, and resolved entity dcids."""

    candidate_svs: list[str]
    candidate_sv_scores: list[float]  # parallel to candidate_svs; 1.0 when score unavailable
    resolved_entity_names: dict[str, str]  # surface name → dcid


async def recall(
    variable: str,
    entities: list[str],
    *,
    graph: EngineGraphClient,
    raw_query: str | None = None,
) -> Recall:
    """Run the recall stage: detect candidate SVs and resolve entity names.

    Args:
        variable: The extracted variable phrase (e.g. "health ODA grants").
        entities: Extracted entity names from the query (e.g. ["USA", "Ethiopia"]).
        graph: Graph client (injected; use FakeGraph in tests).
        raw_query: The original user query, used for detect_svs when provided.
            Falls back to variable when None. The detect endpoint benefits from
            the full query context (entities + variable together).

    Returns:
        A Recall with candidate_svs and resolved_entity_names.
        resolved_entity_names maps surface name to dcid; skips failed resolutions.

    Note:
        Returned SVs are candidates, not confirmed. The engine confirms every SV
        via node reads in the materialise stage.
    """
    detect_query = raw_query or variable
    all_coros = [asyncio.to_thread(graph.detect_svs, detect_query)] + [
        asyncio.to_thread(graph.resolve_entity, name) for name in entities
    ]
    all_results = await asyncio.gather(*all_coros)  # plain; NOT return_exceptions=True
    svs, _entity_dcids, scores = all_results[0]
    resolved: dict[str, str] = {
        name: dcid
        for name, dcid in zip(entities, all_results[1:], strict=True)
        if dcid is not None
    }

    return Recall(
        candidate_svs=svs,
        candidate_sv_scores=scores,
        resolved_entity_names=resolved,
    )
