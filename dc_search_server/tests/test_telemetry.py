"""Tests for telemetry.py."""

from __future__ import annotations

import pytest

from dc_search.telemetry import Usage, step_timer


class TestUsageAdd:
    def test_adds_token_counts(self):
        a = Usage(input_tokens=10, output_tokens=5, model_requests=1)
        b = Usage(input_tokens=20, output_tokens=8, model_requests=1)
        c = a.add(b)
        assert c.input_tokens == 30
        assert c.output_tokens == 13
        assert c.model_requests == 2

    def test_adds_cached_tokens(self):
        a = Usage(cached_input_tokens=3, cached_write_tokens=1)
        b = Usage(cached_input_tokens=7, cached_write_tokens=2)
        c = a.add(b)
        assert c.cached_input_tokens == 10
        assert c.cached_write_tokens == 3

    def test_latency_both_none(self):
        a = Usage()
        b = Usage()
        c = a.add(b)
        assert c.latency_s is None

    def test_latency_one_none(self):
        a = Usage(latency_s=1.5)
        b = Usage(latency_s=None)
        c = a.add(b)
        assert c.latency_s == pytest.approx(1.5)

    def test_latency_both_set(self):
        a = Usage(latency_s=1.0)
        b = Usage(latency_s=2.5)
        c = a.add(b)
        assert c.latency_s == pytest.approx(3.5)

    def test_model_uses_first_non_none(self):
        a = Usage(model="gemini-flash-lite-latest")
        b = Usage(model="gemini-2.5-flash")
        c = a.add(b)
        assert c.model == "gemini-flash-lite-latest"

    def test_model_falls_back_to_other(self):
        a = Usage(model=None)
        b = Usage(model="gemini-2.5-flash")
        c = a.add(b)
        assert c.model == "gemini-2.5-flash"

    def test_immutable(self):
        a = Usage(input_tokens=5)
        with pytest.raises(Exception):
            a.input_tokens = 10  # ty: ignore[invalid-assignment]


class TestStepTimer:
    def test_writes_latency_s(self):
        record: dict = {}
        with step_timer(record):
            pass
        assert "latency_s" in record
        assert record["latency_s"] >= 0.0

    def test_writes_latency_s_on_exception(self):
        record: dict = {}
        with pytest.raises(ValueError):
            with step_timer(record):
                raise ValueError("oops")
        assert "latency_s" in record
