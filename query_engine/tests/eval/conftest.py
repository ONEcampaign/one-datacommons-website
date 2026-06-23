"""Shared fixtures and stubs for the QRE eval test suite."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the parent tests/ directory visible so we can access its helpers.
sys.path.insert(0, str(Path(__file__).parents[1]))

from qre import TimeWindow


class StubGraphClient:
    """In-memory graph client for tests.

    ``known_dcids`` is the set of dcids that ``exists`` returns True for.
    ``counts`` maps (frozenset of stat_vars, frozenset of entities) -> int.
    """

    def __init__(
        self,
        known_dcids: set[str] | None = None,
        counts: dict | None = None,
    ):
        self.known_dcids: set[str] = known_dcids or set()
        self.counts: dict = counts or {}

    def exists(self, dcid: str) -> bool:
        return dcid in self.known_dcids

    def count_observations(
        self,
        *,
        stat_vars: list[str],
        entities: list[str],
        window: TimeWindow | None,
    ) -> int | None:
        key = (frozenset(stat_vars), frozenset(entities))
        return self.counts.get(key)


class RaisingGraphClient:
    """Graph client that always raises to verify fail-loud behavior on graph errors."""

    def exists(self, dcid: str) -> bool:
        raise RuntimeError("graph error: network unavailable")

    def count_observations(self, *, stat_vars, entities, window) -> int | None:
        raise RuntimeError("graph error: network unavailable")


@pytest.fixture
def stub_graph():
    """A StubGraphClient with no pre-loaded dcids (configure in the test)."""
    return StubGraphClient()


@pytest.fixture
def raising_graph():
    return RaisingGraphClient()


def make_worked_example_response() -> dict:
    """Return the worked example response dict (plain function, not a fixture)."""
    from conftest import worked_example_response as _fixture

    return _fixture.__wrapped__()


@pytest.fixture
def worked_example_response() -> dict:
    """Pytest fixture that returns the worked example response dict."""
    return make_worked_example_response()
