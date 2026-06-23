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
    """Graph-backed recall: candidate SVs and resolved entity dcids."""

    candidate_svs: list[str]
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
    candidate_svs, _entity_dcids = await asyncio.to_thread(graph.detect_svs, detect_query)

    resolved: dict[str, str] = {}
    for name in entities:
        dcid = await asyncio.to_thread(graph.resolve_entity, name)
        if dcid is not None:
            resolved[name] = dcid

    return Recall(candidate_svs=candidate_svs, resolved_entity_names=resolved)
