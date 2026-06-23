"""Node-existence confirmation: mints GraphRef from a dcid, dropping unconfirmed nodes.

This is the grounding choke-point: we confirm every dcid the LLM proposed actually
exists in the graph. No fabrication allowed — unconfirmed dcids are dropped.

Raises:
    EngineInfraError — transport or non-2xx failure (re-raised from GraphInfraError).
    GroundingMiss — node is absent (genuine 200 with no data). Callers catch this
        and drop the dcid or convert to no_data.
"""
from __future__ import annotations

from qre.engine.errors import GroundingMiss
from qre.engine.graph import EngineGraphClient
from qre.models import GraphRef


def graphref(dcid: str, *, graph: EngineGraphClient) -> GraphRef:
    """Confirm a dcid exists in the graph and return a GraphRef with its label.

    Args:
        dcid: The graph node identifier to look up.
        graph: Graph client (injected; use FakeGraph in tests).

    Returns:
        A GraphRef with dcid and label from the graph.

    Raises:
        EngineInfraError: On transport error or non-2xx response (re-raised from
            GraphInfraError).
        GroundingMiss: When the node is absent (genuine 200 with empty/missing data).
    """
    label = graph.node_label(dcid)

    if label is None:
        raise GroundingMiss(dcid)

    return GraphRef(dcid=dcid, label=label)


def graphrefs(dcids: list[str], *, graph: EngineGraphClient) -> list[GraphRef]:
    """Confirm each dcid and return only those confirmed by the graph.

    Unlike graphref, GroundingMiss is caught and the dcid is silently dropped.
    GraphInfraError (transport failure) still propagates — infrastructure errors
    must not be swallowed.

    Args:
        dcids: List of graph node identifiers to look up.
        graph: Graph client (injected; use FakeGraph in tests).

    Returns:
        List of confirmed GraphRef objects, in the same order as the input,
        with absent nodes dropped.

    Raises:
        EngineInfraError: On transport error or non-2xx response.
    """
    result: list[GraphRef] = []
    for dcid in dcids:
        try:
            result.append(graphref(dcid, graph=graph))
        except GroundingMiss:
            pass
    return result
