"""Gemini structured-output wrapper for the QRE engine.

Sync API only — the async core calls generate_structured via asyncio.to_thread.
The genai.Client singleton is built lazily; importing this module never touches
the network or requires GEMINI_API_KEY (only an actual call does).

Note: gemini-flash-lite-latest is assumed to resolve to a Gemini 3 model. The
explicit alias check exists because -latest aliases do not contain '3' in the model
string, so a bare 'gemini-3' check would miss them.
"""
from __future__ import annotations

import os
from typing import TypeVar, cast

from pydantic import BaseModel

from qre.engine.config import QRE_ENGINE_MODEL
from qre.engine.errors import LLMInfraError

_SchemaT = TypeVar("_SchemaT", bound=BaseModel)

# Lazy singleton — built on first call to generate_structured.
_CLIENT = None


def _get_client():
    """Return (or build) the singleton genai.Client.

    Raises LLMInfraError only when a live call is attempted and GEMINI_API_KEY
    is absent. Tests never reach this path because they use FakeLLM.
    """
    global _CLIENT
    if _CLIENT is None:
        try:
            from google import genai
        except ImportError as exc:
            raise LLMInfraError(
                "qre.engine requires the 'engine' extra. "
                "Install with: uv sync --extra engine (or pip install 'qre[engine]')."
            ) from exc
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise LLMInfraError(
                "GEMINI_API_KEY is required for live LLM calls. "
                "Set the environment variable or use FakeLLM in tests."
            )
        _CLIENT = genai.Client(api_key=api_key)
    return _CLIENT


def _thinking_config_for_model(model: str):
    """Return the right ThinkingConfig for the model, or None.

    Gemini 3 family (including the -latest aliases that currently resolve to
    Gemini 3) uses thinking_level=MINIMAL. Combined thinking_budget + thinking_level
    in the same request is not supported; the explicit check on model name is
    intentional and must NOT be collapsed to a bare 'gemini-3' in model check —
    the -latest aliases are Gemini 3 but do not contain '3' in the model string.
    """
    from google.genai.types import ThinkingConfig, ThinkingLevel

    if model in ("gemini-flash-latest", "gemini-flash-lite-latest"):
        return ThinkingConfig(thinking_level=ThinkingLevel.MINIMAL)
    if "gemini-3" in model:
        return ThinkingConfig(thinking_level=ThinkingLevel.MINIMAL)
    if "gemini-2.5" in model:
        return ThinkingConfig(thinking_budget=0, include_thoughts=False)
    if "gemma" in model:
        return None
    if "gemini" in model:
        # Unknown gemini-* — forward-compatible safe default.
        return ThinkingConfig(thinking_level=ThinkingLevel.MINIMAL)
    return None


class LLM:
    """Sync Gemini structured-output wrapper.

    Usage::

        llm = LLM()
        result: MySchema = llm.generate_structured(
            prompt="...", system="...", schema=MySchema
        )

    Tests inject FakeLLM (from tests/fixtures/__init__.py) via the engine's
    resolve_async dependency-injection seam. The live LLM is never called in CI.
    """

    def __init__(self, *, model: str | None = None, api_key: str | None = None):
        # api_key is stored for explicit override (e.g. the recorder script).
        # When None, _get_client() falls back to the GEMINI_API_KEY env var.
        self._model = model or QRE_ENGINE_MODEL
        self._api_key_override = api_key

    def _client(self):
        if self._api_key_override:
            # Override: build a dedicated client, not the shared singleton.
            try:
                from google import genai
            except ImportError as exc:
                raise LLMInfraError(
                    "qre.engine requires the 'engine' extra. "
                    "Install with: uv sync --extra engine (or pip install 'qre[engine]')."
                ) from exc
            return genai.Client(api_key=self._api_key_override)
        return _get_client()

    def generate_structured(
        self,
        *,
        prompt: str,
        system: str,
        schema: type[_SchemaT],
    ) -> _SchemaT:
        """Sync structured-output call at temperature 0.

        Raises LLMInfraError on:
        - transport / API error
        - response.parsed is None (schema validation failure)
        """
        try:
            from google.genai.types import GenerateContentConfig
        except ImportError as exc:
            raise LLMInfraError(
                "qre.engine requires the 'engine' extra. "
                "Install with: uv sync --extra engine (or pip install 'qre[engine]')."
            ) from exc

        client = self._client()
        thinking_config = _thinking_config_for_model(self._model)

        config = GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            # Pass the Pydantic class directly so response.parsed is a typed instance.
            response_schema=schema,
            temperature=0.0,
            thinking_config=thinking_config,
        )

        try:
            response = client.models.generate_content(
                model=self._model,
                contents=prompt,
                config=config,
            )
        except Exception as exc:
            raise LLMInfraError(f"Gemini API error: {exc}") from exc

        parsed = response.parsed
        if parsed is None:
            raise LLMInfraError(
                f"LLM output failed schema validation; response.parsed is None "
                f"(model={self._model!r}, schema={schema.__name__!r})"
            )
        # cast recovers the concrete type from the genai stub's wider BaseModel | dict | Enum.
        return cast(schema, parsed)
