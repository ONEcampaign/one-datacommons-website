"""Shared fixtures for tests/engine — applied automatically to every test in this package.

Date pin
--------
The extract() system prompt embeds "Today's date is <today>", which is hashed
into the FakeLLM fixture key.  Pinning the date here keeps all offline engine
tests deterministic regardless of the wall-clock date.

The pin uses the same mechanism as _harness.py (patch qre.engine.extract.date)
so the two are consistent.  The canonical pinned date is 2026-06-23 — the date
on which the extraction fixtures were first recorded.
"""
from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest

_PINNED_DATE = date(2026, 6, 23)


@pytest.fixture(autouse=True)
def _pin_extract_date():
    """Pin qre.engine.extract.date.today() to 2026-06-23 for the whole session.

    This prevents FakeLLM KeyErrors when the wall-clock date advances past the
    fixture recording date.
    """
    with patch("qre.engine.extract.date") as mock_date:
        mock_date.today.return_value = _PINNED_DATE
        # Allow date(*args) construction to still produce real date objects so
        # other parts of the code that call date(year, month, day) are unaffected.
        mock_date.side_effect = lambda *args, **kwargs: date(*args, **kwargs)
        yield
