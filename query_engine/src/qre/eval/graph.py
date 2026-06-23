"""Graph-client seam for the QRE eval harness.

Provides the GraphClient Protocol and DataCommonsGraphClient (backed by the
datacommons-client package).

Graph call failures MUST propagate as exceptions. Evaluators depend on
fail-loud behavior to avoid false 1.0 scores on network or API errors.
"""
import os
import socket
from functools import cache
from typing import Protocol

from qre import TimeWindow


class GraphClient(Protocol):
    """Minimal graph-access interface used by the eval evaluators."""

    def exists(self, dcid: str) -> bool:
        """Return True if the dcid resolves to a known graph node.

        Must raise (never return True) on network or API error.
        """
        ...

    def count_observations(
        self,
        *,
        stat_vars: list[str],
        entities: list[str],
        window: TimeWindow | None,
    ) -> int | None:
        """Count distinct (date, facetId) observation pairs within the window.

        Returns None when the count is genuinely unavailable (the evaluator
        skips the +/-5% check but still requires has_data == True).
        Must raise on network or API error.
        """
        ...


def _in_window(date_str: str, window: TimeWindow | None) -> bool:
    """Return True if date_str (e.g. '2018') falls within the given window."""
    if window is None:
        return True
    try:
        year = int(str(date_str)[:4])
    except (ValueError, TypeError):
        return True  # non-year date strings: include by default
    if window.start_year is not None and year < window.start_year:
        return False
    if window.end_year is not None and year > window.end_year:
        return False
    return True


@cache
def _build_dc_client():
    """Build and cache a DataCommonsClient as a process-lifetime singleton."""
    from datacommons_client.client import DataCommonsClient

    socket.setdefaulttimeout(30)

    url = os.environ.get("QRE_GRAPH_URL")
    api_key = os.environ.get("QRE_GRAPH_API_KEY") or os.environ.get("DC_API_KEY")

    if url and "datacommons.org" not in url:
        return DataCommonsClient(url=f"{url.rstrip('/')}/")
    if not api_key:
        raise RuntimeError(
            "QRE_GRAPH_API_KEY (or DC_API_KEY) is required when QRE_GRAPH_URL targets "
            "datacommons.org or is unset."
        )
    return DataCommonsClient(api_key=api_key)


class DataCommonsGraphClient:
    """GraphClient backed by the datacommons-client package."""

    def __init__(self, dc_client=None):
        # Accepts an explicit client for testing; uses the cached singleton otherwise.
        self._dc = dc_client if dc_client is not None else _build_dc_client()

    def exists(self, dcid: str) -> bool:
        """Return True if dcid has a typeOf arc in the graph.

        Raises on network or API error; never returns True on error.
        """
        resp = self._dc.node.fetch_property_values(node_dcids=dcid, properties="typeOf")
        return bool(resp.extract_connected_dcids(dcid, "typeOf"))

    def count_observations(
        self,
        *,
        stat_vars: list[str],
        entities: list[str],
        window: TimeWindow | None,
    ) -> int | None:
        """Count distinct (date, facetId) pairs for the given stat_vars and entities.

        Returns None when no records are returned (cannot distinguish zero from
        truly unavailable). Raises on network or API error.
        """
        if not stat_vars or not entities:
            return None
        resp = self._dc.observation.fetch_observations_by_entity_dcid(
            date="all",
            entity_dcids=entities,
            variable_dcids=stat_vars,
        )
        records = resp.to_observation_records()
        pairs = {
            (rec.date, rec.facetId)
            for rec in records
            if _in_window(rec.date, window)
        }
        return len(pairs) or None
