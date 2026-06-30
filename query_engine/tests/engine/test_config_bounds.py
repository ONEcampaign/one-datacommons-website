"""Config startup-validation tests.

Each out-of-range env var raises ValueError at import time, before the port
binds. Tests reload the config module with a bad env var, assert the error,
then restore the environment so subsequent imports remain clean.
"""
from __future__ import annotations

import importlib
import os
import sys

import pytest


def _import_config_bad(**env_overrides: str) -> None:
    """Reload config with the given env overrides and expect ValueError.

    Pops the cached module, sets bad env vars, attempts import, then restores
    everything so the next test starts from a clean state.
    """
    old: dict[str, str | None] = {k: os.environ.get(k) for k in env_overrides}
    for k, v in env_overrides.items():
        os.environ[k] = v
    sys.modules.pop("qre.engine.config", None)
    try:
        importlib.import_module("qre.engine.config")
    finally:
        for k, orig_v in old.items():
            if orig_v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = orig_v
        sys.modules.pop("qre.engine.config", None)


class TestNumericBounds:
    def test_max_confirm_candidates_zero(self):
        with pytest.raises(ValueError, match="QRE_MAX_CONFIRM_CANDIDATES"):
            _import_config_bad(QRE_MAX_CONFIRM_CANDIDATES="0")

    def test_relevance_threshold_zero(self):
        with pytest.raises(ValueError, match="QRE_RELEVANCE_THRESHOLD"):
            _import_config_bad(QRE_RELEVANCE_THRESHOLD="0")

    def test_relevance_threshold_above_one(self):
        with pytest.raises(ValueError, match="QRE_RELEVANCE_THRESHOLD"):
            _import_config_bad(QRE_RELEVANCE_THRESHOLD="1.5")

    def test_weak_score_threshold_zero(self):
        with pytest.raises(ValueError, match="QRE_WEAK_SCORE_THRESHOLD"):
            _import_config_bad(QRE_WEAK_SCORE_THRESHOLD="0")

    def test_weak_score_threshold_above_one(self):
        with pytest.raises(ValueError, match="QRE_WEAK_SCORE_THRESHOLD"):
            _import_config_bad(QRE_WEAK_SCORE_THRESHOLD="2.0")

    def test_max_candidates_zero(self):
        with pytest.raises(ValueError, match="QRE_MAX_CANDIDATES"):
            _import_config_bad(QRE_MAX_CANDIDATES="0")

    def test_max_variables_zero(self):
        with pytest.raises(ValueError, match="QRE_MAX_VARIABLES"):
            _import_config_bad(QRE_MAX_VARIABLES="0")

    def test_dominance_margin_negative(self):
        with pytest.raises(ValueError, match="QRE_DOMINANCE_MARGIN"):
            _import_config_bad(QRE_DOMINANCE_MARGIN="-0.1")

    def test_graph_timeout_zero(self):
        with pytest.raises(ValueError, match="QRE_GRAPH_TIMEOUT_S"):
            _import_config_bad(QRE_GRAPH_TIMEOUT_S="0")

    def test_graph_timeout_negative(self):
        with pytest.raises(ValueError, match="QRE_GRAPH_TIMEOUT_S"):
            _import_config_bad(QRE_GRAPH_TIMEOUT_S="-5")

    def test_max_query_chars_zero(self):
        with pytest.raises(ValueError, match="QRE_MAX_QUERY_CHARS"):
            _import_config_bad(QRE_MAX_QUERY_CHARS="0")

    def test_request_timeout_zero(self):
        with pytest.raises(ValueError, match="QRE_REQUEST_TIMEOUT_S"):
            _import_config_bad(QRE_REQUEST_TIMEOUT_S="0")

    def test_request_timeout_negative(self):
        with pytest.raises(ValueError, match="QRE_REQUEST_TIMEOUT_S"):
            _import_config_bad(QRE_REQUEST_TIMEOUT_S="-1")

    def test_variable_concurrency_below_max_variables(self):
        # QRE_MAX_VARIABLE_CONCURRENCY must be >= QRE_MAX_VARIABLES.
        with pytest.raises(ValueError, match="QRE_MAX_VARIABLE_CONCURRENCY"):
            _import_config_bad(
                QRE_MAX_VARIABLES="6",
                QRE_MAX_VARIABLE_CONCURRENCY="3",
            )


class TestGraphBaseScheme:
    def test_ftp_scheme_rejected(self):
        with pytest.raises(ValueError, match="QRE_GRAPH_BASE"):
            _import_config_bad(QRE_GRAPH_BASE="ftp://staging.example.com")

    def test_missing_scheme_rejected(self):
        with pytest.raises(ValueError, match="QRE_GRAPH_BASE"):
            _import_config_bad(QRE_GRAPH_BASE="dc-staging.one.org")

    def test_http_non_local_rejected(self):
        # Plain http:// (non-local) should be rejected.
        with pytest.raises(ValueError, match="QRE_GRAPH_BASE"):
            _import_config_bad(QRE_GRAPH_BASE="http://dc-staging.one.org")

    def test_http_localhost_subdomain_spoof_rejected(self):
        # R4: a prefix check passes http://localhost.evil.com; the hostname check
        # must reject it (hostname is "localhost.evil.com", not "localhost").
        with pytest.raises(ValueError, match="QRE_GRAPH_BASE"):
            _import_config_bad(QRE_GRAPH_BASE="http://localhost.evil.com")

    def test_http_127_0_0_1_suffix_spoof_rejected(self):
        # R4: a prefix check passes http://127.0.0.1.attacker; the hostname check
        # must reject it (hostname is "127.0.0.1.attacker", not "127.0.0.1").
        with pytest.raises(ValueError, match="QRE_GRAPH_BASE"):
            _import_config_bad(QRE_GRAPH_BASE="http://127.0.0.1.attacker")

    def test_https_accepted(self):
        # No error for a valid https:// base.
        _import_config_bad.__doc__  # just to reference something harmless
        old = os.environ.get("QRE_GRAPH_BASE")
        os.environ["QRE_GRAPH_BASE"] = "https://dc-staging.one.org"
        sys.modules.pop("qre.engine.config", None)
        try:
            importlib.import_module("qre.engine.config")  # must not raise
        finally:
            if old is None:
                os.environ.pop("QRE_GRAPH_BASE", None)
            else:
                os.environ["QRE_GRAPH_BASE"] = old
            sys.modules.pop("qre.engine.config", None)

    def test_http_localhost_accepted(self):
        old = os.environ.get("QRE_GRAPH_BASE")
        os.environ["QRE_GRAPH_BASE"] = "http://localhost:8080"
        sys.modules.pop("qre.engine.config", None)
        try:
            importlib.import_module("qre.engine.config")  # must not raise
        finally:
            if old is None:
                os.environ.pop("QRE_GRAPH_BASE", None)
            else:
                os.environ["QRE_GRAPH_BASE"] = old
            sys.modules.pop("qre.engine.config", None)

    def test_http_127_0_0_1_accepted(self):
        old = os.environ.get("QRE_GRAPH_BASE")
        os.environ["QRE_GRAPH_BASE"] = "http://127.0.0.1:8080"
        sys.modules.pop("qre.engine.config", None)
        try:
            importlib.import_module("qre.engine.config")  # must not raise
        finally:
            if old is None:
                os.environ.pop("QRE_GRAPH_BASE", None)
            else:
                os.environ["QRE_GRAPH_BASE"] = old
            sys.modules.pop("qre.engine.config", None)
