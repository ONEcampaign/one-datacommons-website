"""Tests for extraction.py — mocks llm.generate_structured throughout."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from dc_search import llm
from dc_search.extraction import (
    ExtractedDate,
    QueryExtraction,
    _system_instruction,
    extract,
)
from dc_search.telemetry import Usage

_STUB_USAGE = Usage(input_tokens=10, output_tokens=5, model="test-model", model_requests=1)

# ---------------------------------------------------------------------------
# Table-driven test cases
# ---------------------------------------------------------------------------

# Each entry: (query, stubbed_QueryExtraction, optional model_override)
_CASES: list[tuple[str, QueryExtraction, str | None]] = [
    # 1. Single-variable lookup — one entity, one variable, no dates
    (
        "life expectancy in Kenya",
        QueryExtraction(
            entities=["Kenya"],
            dates=[],
            variables=["life expectancy"],
        ),
        None,
    ),
    # 2. Multi-variable — two variables extracted from a conjunctive query
    (
        "life expectancy and population in Kenya",
        QueryExtraction(
            entities=["Kenya"],
            dates=[],
            variables=["life expectancy", "population"],
        ),
        None,
    ),
    # 3. Multi-entity ranking query
    (
        "top 5 countries by GDP in Africa",
        QueryExtraction(
            entities=["Africa"],
            dates=[],
            variables=["GDP"],
        ),
        None,
    ),
    # 4. Timeless query — no dates
    (
        "capital of France",
        QueryExtraction(
            entities=["France"],
            dates=[],
            variables=["capital"],
        ),
        None,
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("query,expected_extraction,model_override", _CASES)
async def test_extract_returns_stubbed_values(
    query: str,
    expected_extraction: QueryExtraction,
    model_override: str | None,
) -> None:
    mock_generate = AsyncMock(return_value=(expected_extraction, _STUB_USAGE))
    with patch("dc_search.extraction.llm.generate_structured", mock_generate):
        result_extraction, result_usage = await extract(query, model=model_override)

    assert result_extraction == expected_extraction
    assert result_usage == _STUB_USAGE


@pytest.mark.asyncio
@pytest.mark.parametrize("query,expected_extraction,model_override", _CASES)
async def test_extract_calls_generate_structured_correctly(
    query: str,
    expected_extraction: QueryExtraction,
    model_override: str | None,
) -> None:
    mock_generate = AsyncMock(return_value=(expected_extraction, _STUB_USAGE))
    with patch("dc_search.extraction.llm.generate_structured", mock_generate):
        await extract(query, model=model_override)

    mock_generate.assert_called_once()
    _, kwargs = mock_generate.call_args
    assert kwargs["schema"] is QueryExtraction
    # System prompt is built per call with today's date substituted in.
    assert "structured-data extraction assistant" in kwargs["system"]
    assert "[[TODAY]]" not in kwargs["system"]
    assert date.today().isoformat() in kwargs["system"]
    assert kwargs["thinking"] is False
    expected_model = model_override if model_override is not None else llm.MODEL
    assert kwargs["model"] == expected_model


@pytest.mark.asyncio
async def test_extract_model_override_is_forwarded() -> None:
    """Passing model= overrides llm.MODEL for the single call."""
    extraction = QueryExtraction(
        entities=["Uganda"],
        dates=[ExtractedDate(kind="range", start="2010")],
        variables=["life expectancy"],
    )
    mock_generate = AsyncMock(return_value=(extraction, _STUB_USAGE))
    custom_model = "gemini-2.5-flash"
    with patch("dc_search.extraction.llm.generate_structured", mock_generate):
        await extract("life expectancy in Uganda since 2010", model=custom_model)

    _, kwargs = mock_generate.call_args
    assert kwargs["model"] == custom_model


@pytest.mark.asyncio
async def test_extract_no_model_override_uses_llm_model() -> None:
    """Omitting model= passes llm.MODEL verbatim."""
    extraction = QueryExtraction(
        entities=[],
        dates=[],
        variables=["GDP per capita"],
    )
    mock_generate = AsyncMock(return_value=(extraction, _STUB_USAGE))
    with patch("dc_search.extraction.llm.generate_structured", mock_generate):
        await extract("GDP per capita")

    _, kwargs = mock_generate.call_args
    assert kwargs["model"] == llm.MODEL


def test_system_instruction_injects_today() -> None:
    """[[TODAY]] is replaced with today's date, with no placeholder left over."""
    prompt = _system_instruction()
    assert date.today().isoformat() in prompt
    assert "[[TODAY]]" not in prompt
