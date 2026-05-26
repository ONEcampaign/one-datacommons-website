"""Tests for config.py."""

from __future__ import annotations

import pytest


def _load_fresh(**env_overrides):
    """Import load_config with a clean cache and patched env."""
    import os
    import sys
    from unittest.mock import patch

    # Always clear the cache before loading
    if "dc_search.config" in sys.modules:
        import dc_search.config as _m

        _m.load_config.cache_clear()

    with patch.dict(os.environ, env_overrides, clear=False):
        from dc_search.config import load_config

        load_config.cache_clear()
        cfg = load_config()
    return cfg


class TestLoadConfig:
    def setup_method(self):
        # Clear cache before each test
        try:
            from dc_search.config import load_config

            load_config.cache_clear()
        except ImportError:
            pass

    def test_defaults_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("DC_API_URL", raising=False)
        monkeypatch.delenv("DC_API_KEY", raising=False)
        monkeypatch.delenv("DC_SEARCH_MODEL", raising=False)
        monkeypatch.delenv("DC_SEARCH_INITIAL_K", raising=False)
        monkeypatch.delenv("DC_SEARCH_MAX_SHAPES", raising=False)
        from dc_search.config import load_config

        load_config.cache_clear()
        cfg = load_config()
        assert cfg.api_url == "http://localhost:8081/v2"
        assert cfg.api_key is None
        assert cfg.model == "gemini-flash-lite-latest"
        # shapecap10 defaults when the env vars are unset.
        assert cfg.initial_k == 80
        assert cfg.max_shapes == 10

    def test_shapecap_env_overrides(self, monkeypatch):
        monkeypatch.setenv("DC_SEARCH_INITIAL_K", "50")
        monkeypatch.setenv("DC_SEARCH_MAX_SHAPES", "6")
        from dc_search.config import load_config

        load_config.cache_clear()
        cfg = load_config()
        assert cfg.initial_k == 50
        assert cfg.max_shapes == 6

    def test_max_shapes_zero_disables_cap(self, monkeypatch):
        monkeypatch.setenv("DC_SEARCH_MAX_SHAPES", "0")
        from dc_search.config import load_config

        load_config.cache_clear()
        cfg = load_config()
        assert cfg.max_shapes is None

    def test_initial_k_must_be_positive(self, monkeypatch):
        monkeypatch.setenv("DC_SEARCH_INITIAL_K", "0")
        from dc_search.config import load_config

        load_config.cache_clear()
        with pytest.raises(ValueError):
            load_config()

    def test_max_shapes_rejects_negative(self, monkeypatch):
        monkeypatch.setenv("DC_SEARCH_MAX_SHAPES", "-3")
        from dc_search.config import load_config

        load_config.cache_clear()
        with pytest.raises(ValueError):
            load_config()

    def test_custom_localhost_url(self, monkeypatch):
        monkeypatch.setenv("DC_API_URL", "http://localhost:9999/v2")
        from dc_search.config import load_config

        load_config.cache_clear()
        cfg = load_config()
        assert cfg.api_url == "http://localhost:9999/v2"

    def test_trailing_slash_stripped(self, monkeypatch):
        monkeypatch.setenv("DC_API_URL", "http://localhost:8081/v2/")
        from dc_search.config import load_config

        load_config.cache_clear()
        cfg = load_config()
        assert not cfg.api_url.endswith("/")

    def test_raises_on_non_localhost_url(self, monkeypatch):
        monkeypatch.setenv("DC_API_URL", "http://evil.com/api")
        from dc_search.config import load_config

        load_config.cache_clear()
        with pytest.raises(ValueError, match="DC_API_URL must start with one of"):
            load_config()

    def test_raises_on_https_external(self, monkeypatch):
        monkeypatch.setenv("DC_API_URL", "https://api.datacommons.org/v2")
        from dc_search.config import load_config

        load_config.cache_clear()
        with pytest.raises(ValueError, match="DC_API_URL must start with one of"):
            load_config()

    def test_api_key_read(self, monkeypatch):
        monkeypatch.setenv("DC_API_URL", "http://localhost:8081/v2")
        monkeypatch.setenv("DC_API_KEY", "test-key-123")
        from dc_search.config import load_config

        load_config.cache_clear()
        cfg = load_config()
        assert cfg.api_key == "test-key-123"

    def test_model_env_var(self, monkeypatch):
        monkeypatch.setenv("DC_API_URL", "http://localhost:8081/v2")
        monkeypatch.setenv("DC_SEARCH_MODEL", "gemini-2.5-flash")
        from dc_search.config import load_config

        load_config.cache_clear()
        cfg = load_config()
        assert cfg.model == "gemini-2.5-flash"

    def test_resolve_target_default(self, monkeypatch):
        monkeypatch.delenv("DC_RESOLVE_TARGET", raising=False)
        from dc_search.config import load_config

        load_config.cache_clear()
        cfg = load_config()
        assert cfg.resolve_target == "base_and_custom"

    def test_resolve_target_env_override(self, monkeypatch):
        monkeypatch.setenv("DC_RESOLVE_TARGET", "custom_only")
        from dc_search.config import load_config

        load_config.cache_clear()
        cfg = load_config()
        assert cfg.resolve_target == "custom_only"

    def test_resolve_target_invalid_raises(self, monkeypatch):
        monkeypatch.setenv("DC_RESOLVE_TARGET", "bad_value")
        from dc_search.config import load_config

        load_config.cache_clear()
        with pytest.raises(ValueError, match="DC_RESOLVE_TARGET must be one of"):
            load_config()
