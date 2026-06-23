"""Tests for qre.engine.extract — extraction stage with FakeLLM.

All tests are offline and replay from llm_responses.json.
"""
from __future__ import annotations

import asyncio

from qre.engine.extract import ExtractedDate, Extraction, extract
from tests.fixtures import FakeLLM

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    return asyncio.run(coro)




def test_extract_health_oda_grants_usa_eth():
    """df-01: health ODA grants from USA to Ethiopia → variables + entities."""
    result = _run(extract(
        "health ODA grants from USA to Ethiopia",
        llm=FakeLLM(),
    ))
    assert isinstance(result, Extraction)
    assert any("health" in v.lower() for v in result.variables)
    assert "USA" in result.entities or "United States" in result.entities
    assert "Ethiopia" in result.entities
    assert result.dates == []


def test_extract_hiv_aids_oda_grants_usa_ken():
    """df-05: HIV/AIDS ODA grants from USA to Kenya → variables + entities."""
    result = _run(extract(
        "HIV/AIDS ODA grants from USA to Kenya",
        llm=FakeLLM(),
    ))
    assert isinstance(result, Extraction)
    assert any("hiv" in v.lower() or "aids" in v.lower() for v in result.variables)
    assert "Kenya" in result.entities


def test_extract_oda_germany_eth():
    """df-04: official development assistance from Germany to Ethiopia."""
    result = _run(extract(
        "official development assistance from Germany to Ethiopia",
        llm=FakeLLM(),
    ))
    assert isinstance(result, Extraction)
    assert len(result.variables) >= 1
    assert "Germany" in result.entities or "Deutschland" in result.entities
    assert "Ethiopia" in result.entities


def test_extract_health_aid_kenya():
    """df-09: health aid to Kenya — no scheme named (scheme becomes unbound)."""
    result = _run(extract(
        "health aid to Kenya",
        llm=FakeLLM(),
    ))
    assert isinstance(result, Extraction)
    assert any("health" in v.lower() or "aid" in v.lower() for v in result.variables)
    assert "Kenya" in result.entities


def test_extract_education_oda_india():
    """df-10: education ODA to India — set-binding case."""
    result = _run(extract(
        "education ODA to India",
        llm=FakeLLM(),
    ))
    assert isinstance(result, Extraction)
    assert any("education" in v.lower() for v in result.variables)
    assert "India" in result.entities


def test_extract_health_oda_to_ethiopia():
    """df-06: health ODA grants to Ethiopia — no donor named."""
    result = _run(extract(
        "health ODA grants to Ethiopia",
        llm=FakeLLM(),
    ))
    assert isinstance(result, Extraction)
    assert any("health" in v.lower() for v in result.variables)
    assert "Ethiopia" in result.entities




def test_extract_with_year_range():
    """GDP since 2010 → range date with start=2010."""
    result = _run(extract(
        "GDP per capita in Kenya since 2010",
        llm=FakeLLM(),
    ))
    assert isinstance(result, Extraction)
    assert len(result.dates) >= 1
    d = result.dates[0]
    assert d.kind == "range"
    assert d.start == "2010"




def test_extraction_is_pydantic_model():
    """Extraction is a BaseModel — model_validate and field access work."""
    data = {
        "variables": ["health ODA"],
        "entities": ["Ethiopia"],
        "dates": [],
    }
    obj = Extraction.model_validate(data)
    assert obj.variables == ["health ODA"]
    assert obj.entities == ["Ethiopia"]
    assert obj.dates == []


def test_extracted_date_kinds():
    """ExtractedDate accepts all three kinds."""
    point = ExtractedDate(kind="point", start="2020", end=None)
    rng = ExtractedDate(kind="range", start="2010", end="2020")
    latest = ExtractedDate(kind="latest", start=None, end=None)
    assert point.kind == "point"
    assert rng.start == "2010"
    assert latest.kind == "latest"


def test_extraction_empty_entities_is_valid():
    """Extraction with empty entities is valid (query with no place)."""
    data = {"variables": ["life expectancy"], "entities": [], "dates": []}
    obj = Extraction.model_validate(data)
    assert obj.entities == []
