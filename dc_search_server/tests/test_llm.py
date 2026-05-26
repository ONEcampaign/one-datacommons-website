"""Tests for dc_search.llm — google-genai wrapper."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from google.genai import errors as genai_errors
from pydantic import BaseModel

from dc_search import llm
from dc_search.telemetry import Usage

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


class _Schema(BaseModel):
    value: str
    count: int


def _make_mock_response(parsed: Any, prompt_tokens: int = 10, output_tokens: int = 5) -> MagicMock:
    """Build a mock GenerateContentResponse."""
    meta = MagicMock()
    meta.prompt_token_count = prompt_tokens
    meta.candidates_token_count = output_tokens
    meta.cached_content_token_count = 2

    resp = MagicMock()
    resp.parsed = parsed
    resp.usage_metadata = meta
    return resp


@pytest.fixture(autouse=True)
def _reset_client():
    """Reset the module-level _CLIENT singleton between tests."""
    original = llm._CLIENT
    llm._CLIENT = None
    yield
    llm._CLIENT = original


# ---------------------------------------------------------------------------
# get_client
# ---------------------------------------------------------------------------


def test_get_client_raises_without_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="GEMINI_API_KEY"):
        llm.get_client()


def test_get_client_singleton(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    c1 = llm.get_client()
    c2 = llm.get_client()
    assert c1 is c2


# ---------------------------------------------------------------------------
# generate_structured — happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_structured_returns_parsed_instance(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    parsed_instance = _Schema(value="hello", count=3)
    mock_response = _make_mock_response(parsed_instance, prompt_tokens=20, output_tokens=8)

    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    llm._CLIENT = mock_client

    result_parsed, result_usage = await llm.generate_structured(
        prompt="test prompt",
        system="test system",
        schema=_Schema,
        model="gemini-flash-lite-latest",
    )

    assert isinstance(result_parsed, _Schema)
    assert result_parsed.value == "hello"
    assert result_parsed.count == 3
    assert isinstance(result_usage, Usage)


# ---------------------------------------------------------------------------
# to_usage — field mapping
# ---------------------------------------------------------------------------


def test_to_usage_maps_fields_correctly():
    meta = MagicMock()
    meta.prompt_token_count = 15
    meta.candidates_token_count = 7
    meta.cached_content_token_count = 3

    resp = MagicMock()
    resp.usage_metadata = meta

    usage = llm.to_usage(resp, "gemini-flash-lite-latest", latency_s=0.42)

    assert usage.input_tokens == 15
    assert usage.output_tokens == 7
    assert usage.cached_input_tokens == 3
    assert usage.model == "gemini-flash-lite-latest"
    assert usage.latency_s == pytest.approx(0.42)
    assert usage.model_requests == 1


def test_to_usage_handles_missing_metadata():
    resp = MagicMock()
    del resp.usage_metadata  # AttributeError path
    resp.usage_metadata = None  # override to None

    usage = llm.to_usage(resp, "gemini-flash-lite-latest", latency_s=None)

    assert usage.model_requests == 1
    assert usage.input_tokens == 0


# ---------------------------------------------------------------------------
# thinking_config gating — N3
# ---------------------------------------------------------------------------


def test_thinking_config_gemini3_flash_lite_latest():
    """gemini-flash-lite-latest (Gemini 3 alias) → thinking_level set, no thinking_budget."""
    cfg = llm._thinking_config_for_model("gemini-flash-lite-latest", thinking=False)
    assert cfg is not None
    # SDK may return a ThinkingLevel enum; compare case-insensitively.
    assert str(cfg.thinking_level).upper() in ("MINIMAL", "THINKINGLEVEL.MINIMAL")
    assert not hasattr(cfg, "thinking_budget") or cfg.thinking_budget is None


def test_thinking_config_gemini25_uses_budget():
    """gemini-2.5-pro → thinking_budget=0, no thinking_level."""
    cfg = llm._thinking_config_for_model("gemini-2.5-pro", thinking=False)
    assert cfg is not None
    assert cfg.thinking_budget == 0
    assert cfg.include_thoughts is False
    assert not hasattr(cfg, "thinking_level") or cfg.thinking_level is None


def test_thinking_config_gemma_returns_none():
    """gemma-2-9b → no ThinkingConfig (not supported)."""
    cfg = llm._thinking_config_for_model("gemma-2-9b", thinking=False)
    assert cfg is None


def test_thinking_config_thinking_true_returns_none():
    """thinking=True → no ThinkingConfig regardless of model."""
    assert llm._thinking_config_for_model("gemini-flash-lite-latest", thinking=True) is None
    assert llm._thinking_config_for_model("gemini-2.5-pro", thinking=True) is None
    assert llm._thinking_config_for_model("gemma-2-9b", thinking=True) is None


@pytest.mark.asyncio
async def test_generate_structured_passes_thinking_level_for_gemini3(monkeypatch):
    """generate_structured passes thinking_level='minimal' for gemini-flash-lite-latest."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    parsed_instance = _Schema(value="x", count=0)
    mock_response = _make_mock_response(parsed_instance)

    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    llm._CLIENT = mock_client

    await llm.generate_structured(
        prompt="p",
        system=None,
        schema=_Schema,
        model="gemini-flash-lite-latest",
        thinking=False,
    )

    call_args = mock_client.aio.models.generate_content.call_args
    config = call_args.kwargs["config"]
    assert config.thinking_config is not None
    assert str(config.thinking_config.thinking_level).upper() in (
        "MINIMAL",
        "THINKINGLEVEL.MINIMAL",
    )


@pytest.mark.asyncio
async def test_generate_structured_passes_budget_for_gemini25(monkeypatch):
    """generate_structured passes thinking_budget=0 for gemini-2.5-flash."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    parsed_instance = _Schema(value="x", count=0)
    mock_response = _make_mock_response(parsed_instance)

    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    llm._CLIENT = mock_client

    await llm.generate_structured(
        prompt="p",
        system=None,
        schema=_Schema,
        model="gemini-2.5-flash",
        thinking=False,
    )

    call_args = mock_client.aio.models.generate_content.call_args
    config = call_args.kwargs["config"]
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_budget == 0


@pytest.mark.asyncio
async def test_generate_structured_no_thinking_config_for_gemma(monkeypatch):
    """generate_structured passes thinking_config=None for gemma models."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    parsed_instance = _Schema(value="x", count=0)
    mock_response = _make_mock_response(parsed_instance)

    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    llm._CLIENT = mock_client

    await llm.generate_structured(
        prompt="p",
        system=None,
        schema=_Schema,
        model="gemma-3-27b-it",
        thinking=False,
    )

    call_args = mock_client.aio.models.generate_content.call_args
    config = call_args.kwargs["config"]
    assert config.thinking_config is None


@pytest.mark.asyncio
async def test_generate_structured_no_thinking_config_when_thinking_true(monkeypatch):
    """generate_structured passes thinking_config=None when thinking=True."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    parsed_instance = _Schema(value="x", count=0)
    mock_response = _make_mock_response(parsed_instance)

    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    llm._CLIENT = mock_client

    await llm.generate_structured(
        prompt="p",
        system=None,
        schema=_Schema,
        model="gemini-2.5-pro",
        thinking=True,
    )

    call_args = mock_client.aio.models.generate_content.call_args
    config = call_args.kwargs["config"]
    assert config.thinking_config is None


# ---------------------------------------------------------------------------
# Model default: when model=None, uses llm.MODEL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_structured_uses_module_model_default(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(llm, "MODEL", "gemini-flash-lite-latest")

    parsed_instance = _Schema(value="x", count=0)
    mock_response = _make_mock_response(parsed_instance)

    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    llm._CLIENT = mock_client

    await llm.generate_structured(
        prompt="p",
        system=None,
        schema=_Schema,
        # model not passed — should fall back to llm.MODEL
    )

    call_args = mock_client.aio.models.generate_content.call_args
    assert call_args.kwargs["model"] == "gemini-flash-lite-latest"


# ---------------------------------------------------------------------------
# S5: response.parsed is None raises ValueError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_structured_raises_on_none_parsed(monkeypatch):
    """generate_structured raises ValueError when response.parsed is None.

    google-genai sets response.parsed to None when internal schema validation
    fails.  Callers rely on generate_structured raising rather than returning
    None silently.  slot_binding.bind's existing except block catches it.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    mock_response = _make_mock_response(parsed=None)

    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    llm._CLIENT = mock_client

    with pytest.raises(ValueError, match="response.parsed is None"):
        await llm.generate_structured(
            prompt="p",
            system=None,
            schema=_Schema,
            model="gemini-flash-lite-latest",
        )


# ---------------------------------------------------------------------------
# Explicit context caching
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_cache_state(monkeypatch):
    """Isolate the module-level cache registry/flags between tests."""
    llm._system_caches.clear()
    monkeypatch.setattr(llm, "_CACHE_ENABLED", True)
    monkeypatch.setattr(llm, "_CACHE_TTL_S", 3600)
    yield
    llm._system_caches.clear()


def _mock_cache(name: str) -> MagicMock:
    cache = MagicMock()
    cache.name = name  # MagicMock(name=...) sets repr, not .name — assign explicitly.
    return cache


@pytest.mark.asyncio
async def test_get_system_cache_creates_and_reuses(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    mock_client = MagicMock()
    mock_client.aio.caches.create = AsyncMock(return_value=_mock_cache("cachedContents/abc"))
    llm._CLIENT = mock_client

    name1 = await llm.get_system_cache(system="big stable prompt", model="gemini-flash-lite-latest")
    name2 = await llm.get_system_cache(system="big stable prompt", model="gemini-flash-lite-latest")

    assert name1 == "cachedContents/abc"
    assert name2 == "cachedContents/abc"
    # Second call reuses the live entry — no second create.
    assert mock_client.aio.caches.create.await_count == 1
    # The create carried the system prompt and a ttl.
    cfg = mock_client.aio.caches.create.await_args.kwargs["config"]
    assert cfg.system_instruction == "big stable prompt"
    assert cfg.ttl == "3600s"


@pytest.mark.asyncio
async def test_get_system_cache_disabled_returns_none(monkeypatch):
    monkeypatch.setattr(llm, "_CACHE_ENABLED", False)
    mock_client = MagicMock()
    mock_client.aio.caches.create = AsyncMock(return_value=_mock_cache("cachedContents/x"))
    llm._CLIENT = mock_client

    assert await llm.get_system_cache(system="p", model="m") is None
    mock_client.aio.caches.create.assert_not_called()


@pytest.mark.asyncio
async def test_get_system_cache_returns_none_on_create_failure(monkeypatch):
    """Below-minimum prompts / API errors degrade to None (caller passes inline)."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    mock_client = MagicMock()
    mock_client.aio.caches.create = AsyncMock(
        side_effect=genai_errors.ClientError(400, {"error": {"message": "too small"}})
    )
    llm._CLIENT = mock_client

    assert await llm.get_system_cache(system="tiny", model="m") is None


@pytest.mark.asyncio
async def test_invalidate_system_cache_forces_recreate(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    mock_client = MagicMock()
    mock_client.aio.caches.create = AsyncMock(
        side_effect=[_mock_cache("cachedContents/one"), _mock_cache("cachedContents/two")]
    )
    llm._CLIENT = mock_client

    first = await llm.get_system_cache(system="p", model="m")
    llm.invalidate_system_cache(system="p", model="m")
    second = await llm.get_system_cache(system="p", model="m")

    assert first == "cachedContents/one"
    assert second == "cachedContents/two"
    assert mock_client.aio.caches.create.await_count == 2


@pytest.mark.asyncio
async def test_generate_structured_uses_cached_content(monkeypatch):
    """When cached_content is set, it is passed and system_instruction is omitted."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    mock_response = _make_mock_response(_Schema(value="x", count=0))
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    llm._CLIENT = mock_client

    await llm.generate_structured(
        prompt="p",
        system="inline system",
        schema=_Schema,
        model="gemini-flash-lite-latest",
        cached_content="cachedContents/abc",
    )

    config = mock_client.aio.models.generate_content.call_args.kwargs["config"]
    assert config.cached_content == "cachedContents/abc"
    assert config.system_instruction is None


@pytest.mark.asyncio
async def test_generate_structured_inline_system_without_cache(monkeypatch):
    """Without cached_content, the system instruction is passed inline."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    mock_response = _make_mock_response(_Schema(value="x", count=0))
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(return_value=mock_response)
    llm._CLIENT = mock_client

    await llm.generate_structured(
        prompt="p", system="inline system", schema=_Schema, model="gemini-flash-lite-latest"
    )

    config = mock_client.aio.models.generate_content.call_args.kwargs["config"]
    assert config.cached_content is None
    assert config.system_instruction == "inline system"


@pytest.mark.asyncio
async def test_generate_structured_retries_inline_on_cache_404(monkeypatch):
    """A 404 (expired/missing cache) drops the cache and retries inline once."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(llm, "MODEL", "gemini-flash-lite-latest")
    # Seed a live cache entry so we can assert it gets invalidated.
    key = llm._cache_key("inline system", "gemini-flash-lite-latest")
    llm._system_caches[key] = llm._CacheEntry(
        name="cachedContents/stale", expires_at=time.monotonic() + 9999
    )

    ok = _make_mock_response(_Schema(value="ok", count=1))
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(
        side_effect=[genai_errors.ClientError(404, {"error": {"message": "cache gone"}}), ok]
    )
    llm._CLIENT = mock_client

    parsed, _ = await llm.generate_structured(
        prompt="p",
        system="inline system",
        schema=_Schema,
        model="gemini-flash-lite-latest",
        cached_content="cachedContents/stale",
    )

    assert parsed.value == "ok"
    assert mock_client.aio.models.generate_content.await_count == 2
    # Retry was inline: cache dropped, system passed directly.
    retry_config = mock_client.aio.models.generate_content.await_args_list[1].kwargs["config"]
    assert retry_config.cached_content is None
    assert retry_config.system_instruction == "inline system"
    assert key not in llm._system_caches


@pytest.mark.asyncio
async def test_generate_structured_reraises_non_404_client_error(monkeypatch):
    """A 400 (not a cache problem) propagates without an inline retry."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    mock_client = MagicMock()
    mock_client.aio.models.generate_content = AsyncMock(
        side_effect=genai_errors.ClientError(400, {"error": {"message": "bad request"}})
    )
    llm._CLIENT = mock_client

    with pytest.raises(genai_errors.ClientError):
        await llm.generate_structured(
            prompt="p",
            system="inline system",
            schema=_Schema,
            model="gemini-flash-lite-latest",
            cached_content="cachedContents/abc",
        )
    assert mock_client.aio.models.generate_content.await_count == 1
