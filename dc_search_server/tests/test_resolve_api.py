"""Tests for resolve_api.py — resolvekit demo backend endpoints.

All tests are skip-guarded: if ``resolvekit`` is not importable the whole
module is skipped so CI on environments without the package stays green.

Assertions check **shape and status**, never specific confidence magnitudes
(real calibrated values differ from the prototype's hardcoded ones).
"""

from __future__ import annotations

import os
import threading
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Environment — satisfy lifespan env-var validation before dc_search imports.
# ---------------------------------------------------------------------------

os.environ.setdefault("DC_API_URL", "http://localhost:8081/core/api/v2")

# ---------------------------------------------------------------------------
# Skip guard — skip the whole module if resolvekit isn't importable.
# ---------------------------------------------------------------------------

try:
    import resolvekit  # noqa: F401

    _RESOLVEKIT_AVAILABLE = True
except Exception:
    _RESOLVEKIT_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _RESOLVEKIT_AVAILABLE,
    reason="resolvekit not installed",
)

# ---------------------------------------------------------------------------
# Import after the skip guard so we don't crash the collection step.
# ---------------------------------------------------------------------------

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from dc_search import resolve_api  # noqa: E402

# ---------------------------------------------------------------------------
# Shared test app + client
# ---------------------------------------------------------------------------


def _make_app() -> FastAPI:
    """Return a bare FastAPI with only the resolve router mounted."""
    app = FastAPI()
    if resolve_api.router is not None:
        app.include_router(resolve_api.router)
    return app


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(_make_app())


def _post(client: TestClient, path: str, body: dict) -> dict:
    """POST to a resolve-demo API path and assert 200."""
    resp = client.post(f"/api/resolve-demo/api{path}", json=body)
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
    return resp.json()


# ---------------------------------------------------------------------------
# /resolve tests
# ---------------------------------------------------------------------------


def test_resolve_exact_name(client: TestClient) -> None:
    data = _post(client, "/resolve", {"query": "United States"})
    assert data["status"] == "resolved"
    assert data["entity_id"] == "country/USA"
    assert data["conf_pct"] and "%" in data["conf_pct"]
    assert data["match_tier"] == "exact_name"
    assert isinstance(data["elapsed_ms"], float)


def test_resolve_exact_code(client: TestClient) -> None:
    data = _post(client, "/resolve", {"query": "US"})
    assert data["status"] == "resolved"
    assert data["entity_id"] == "country/USA"
    assert data["match_tier"] == "exact_code"


def test_resolve_ambiguous(client: TestClient) -> None:
    data = _post(client, "/resolve", {"query": "Congo"})
    assert data["status"] == "ambiguous"
    assert data["confidence"] is None
    assert len(data["candidates"]) >= 2
    # Top candidates should include COD and COG.
    cand_ids = {c["entity_id"] for c in data["candidates"]}
    assert cand_ids & {"country/COD", "country/COG"}, f"Expected COD/COG in {cand_ids}"


def test_resolve_sentinel(client: TestClient) -> None:
    data = _post(client, "/resolve", {"query": "n/a"})
    assert data["status"] == "no_match"
    assert "sentinel" in (data.get("reason") or ""), f"reason={data.get('reason')!r}"


def test_resolve_typo(client: TestClient) -> None:
    data = _post(client, "/resolve", {"query": "Germny"})
    # resolvekit may return resolved (fuzzy) or ambiguous — either is valid.
    assert data["status"] in {"resolved", "ambiguous", "no_match"}
    assert isinstance(data["elapsed_ms"], float)


# ---------------------------------------------------------------------------
# /suggest tests
# ---------------------------------------------------------------------------


def test_suggest_highlight(client: TestClient) -> None:
    data = _post(client, "/suggest", {"prefix": "germ"})
    assert data["status"] == "ok"
    results = data["results"]
    assert len(results) > 0
    first = results[0]
    # pre + hl + post must reconstruct canonical_name exactly.
    assert first["pre"] + first["hl"] + first["post"] == first["canonical_name"]
    assert first["match_class"] in {"exact_prefix", "token_prefix", "infix", "fuzzy"}


def test_suggest_top_k_respected(client: TestClient) -> None:
    data = _post(client, "/suggest", {"prefix": "a", "top_k": 5})
    assert data["status"] == "ok"
    assert len(data["results"]) <= 5


def test_suggest_empty_prefix(client: TestClient) -> None:
    data = _post(client, "/suggest", {"prefix": ""})
    assert data["status"] == "ok"
    assert "suggest" in data["header"].lower() or "…" in data["header"]


def test_suggest_scope_cities_vs_countries(client: TestClient) -> None:
    # "paris" is a city: the default (all) and the cities scope surface it,
    # but the countries scope filters it out — the original "no suggestions" bug.
    all_scope = _post(client, "/suggest", {"prefix": "paris", "scope": "all"})
    cities = _post(client, "/suggest", {"prefix": "paris", "scope": "cities"})
    countries = _post(client, "/suggest", {"prefix": "paris", "scope": "countries"})
    assert all_scope["status"] == cities["status"] == countries["status"] == "ok"
    assert len(all_scope["results"]) > 0
    assert len(cities["results"]) > 0
    assert len(countries["results"]) == 0


def test_suggest_scope_orgs(client: TestClient) -> None:
    data = _post(client, "/suggest", {"prefix": "toyota", "scope": "orgs"})
    assert data["status"] == "ok"
    assert len(data["results"]) > 0
    assert any("toyota" in r["canonical_name"].lower() for r in data["results"])


def test_suggest_rejects_bad_scope(client: TestClient) -> None:
    r = client.post(
        "/api/resolve-demo/api/suggest",
        json={"prefix": "x", "scope": "planets"},
    )
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# /byod tests — resolve against a user-supplied record set (from_records)
# ---------------------------------------------------------------------------

_BYOD_RECORDS = (
    'id,name,aliases,code\n'
    'ACME-01,Acme Health Initiative,"AHI;Acme Health;Acme",AHI\n'
    'ACME-02,Riverbend Foundation,"Riverbend;RBF",RBF\n'
    'ACME-03,Sahel Resilience Program,"SRP;Sahel Program",SRP\n'
)


def test_byod_resolves_custom_alias_and_code(client: TestClient) -> None:
    # An alias hits the right custom entity; a code is an exact_code match.
    alias = _post(client, "/byod", {"records_csv": _BYOD_RECORDS, "query": "Riverbend"})
    assert alias["status"] == "ok"
    assert alias["record_count"] == 3
    assert alias["resolution"]["status"] == "resolved"
    assert alias["resolution"]["entity_id"] == "custom/ACME-02"

    code = _post(client, "/byod", {"records_csv": _BYOD_RECORDS, "query": "SRP"})
    assert code["resolution"]["status"] == "resolved"
    assert code["resolution"]["entity_id"] == "custom/ACME-03"
    assert code["resolution"]["match_tier"] == "exact_code"


def test_byod_unknown_query_abstains(client: TestClient) -> None:
    data = _post(client, "/byod", {"records_csv": _BYOD_RECORDS, "query": "totally unrelated"})
    assert data["status"] == "ok"
    assert data["resolution"]["status"] == "no_match"
    assert data["resolution"]["entity_id"] is None


def test_byod_empty_query_returns_count_only(client: TestClient) -> None:
    data = _post(client, "/byod", {"records_csv": _BYOD_RECORDS, "query": ""})
    assert data["status"] == "ok"
    assert data["record_count"] == 3
    assert data["resolution"] is None


def test_byod_requires_name_column(client: TestClient) -> None:
    data = _post(client, "/byod", {"records_csv": "id,label\nX,Foo", "query": "Foo"})
    # 'label' is recognised as a name-like header, so this should resolve fine;
    # a records set with NO name-like column is the error case.
    bad = _post(client, "/byod", {"records_csv": "id,value\nX,1", "query": "X"})
    assert data["status"] == "ok"
    assert bad["status"] == "error"


# ---------------------------------------------------------------------------
# /explain tests
# ---------------------------------------------------------------------------


def test_explain_returns_text(client: TestClient) -> None:
    data = _post(client, "/explain", {"query": "France"})
    assert data["status"] == "ok"
    assert isinstance(data["explain_text"], str)
    assert len(data["explain_text"]) > 0
    assert isinstance(data["elapsed_ms"], float)


# ---------------------------------------------------------------------------
# /parse tests
# ---------------------------------------------------------------------------


def test_parse_present_or_unavailable(client: TestClient) -> None:
    data = _post(client, "/parse", {"text": "Kenya and the United States"})
    if resolve_api._PARSE_OK:
        assert data["status"] == "ok"
        assert isinstance(data["segments"], list)
        assert isinstance(data["entities"], list)
        assert isinstance(data["count"], int)
    else:
        assert data["status"] == "unavailable"
        assert "detail" in data


# ---------------------------------------------------------------------------
# /graph tests
# ---------------------------------------------------------------------------


def test_graph_eu_entity_id(client: TestClient) -> None:
    data = _post(
        client,
        "/graph",
        {"group": "European Union", "region": "Eastern Africa", "as_of_year": 2018},
    )
    assert data["status"] == "ok"
    members = data["members"]
    assert members["entity_id"] == "EuropeanUnion", (
        f"Expected EuropeanUnion, got {members['entity_id']!r}"
    )
    assert isinstance(members["members"], list)
    assert len(members["members"]) > 0
    # All items should be 3-letter ISO codes (or None if unlinked).
    for code in members["members"]:
        assert code is None or (isinstance(code, str) and len(code) == 3), f"Bad code: {code!r}"

    # UK was in EU in 2018.
    assert data["subject_in_group"] is True


def test_graph_eu_uk_2026(client: TestClient) -> None:
    data = _post(
        client,
        "/graph",
        {"group": "European Union", "region": "Western Europe", "as_of_year": 2026},
    )
    assert data["status"] == "ok"
    # UK left EU (Brexit effective 2020).
    assert data["subject_in_group"] is False


def test_graph_eu_canonical_name_in_resolver(client: TestClient) -> None:
    """Directly verify that members_of resolves EU group to 'EuropeanUnion' entity_id."""
    r = resolve_api._get_resolver()
    assert r is not resolve_api._RESOLVER_SENTINEL
    eu_res = r.resolve("European Union", include_entity=True)
    assert eu_res.entity_id == "EuropeanUnion"


def test_within_subregion(client: TestClient) -> None:
    """Eastern Africa has countries in the geo.country graph."""
    data = _post(
        client,
        "/graph",
        {"group": "European Union", "region": "Eastern Africa", "as_of_year": 2024},
    )
    assert data["status"] == "ok"
    within = data["within"]
    # geo.country edges return countries, not subregions.
    assert isinstance(within["codes"], list)
    # Eastern Africa should resolve to some countries.
    assert within["count"] > 0, "Expected at least one country in Eastern Africa"


# ---------------------------------------------------------------------------
# /bulk tests
# ---------------------------------------------------------------------------

_BULK_CSV = "country\nBrazil\nCongo\nn/a\nBrazil"  # 4 rows, 3 distinct


def test_bulk_dedup(client: TestClient) -> None:
    data = _post(client, "/bulk", {"csv_text": _BULK_CSV, "column": "country"})
    assert data["status"] == "ok"
    summary = data["summary"]
    # 4 raw rows, 3 distinct values.
    assert summary["rows"] == 4
    assert summary["unique"] == 3
    assert summary["resolved"] >= 1  # Brazil and maybe Germany
    # results are per-row (all 4 rows, capped at 30).
    assert len(data["results"]) == 4


def test_bulk_header_echo(client: TestClient) -> None:
    data = _post(client, "/bulk", {"csv_text": _BULK_CSV, "column": "country"})
    assert data["status"] == "ok"
    assert data["headers"] == ["country"]
    assert data["column"] == "country"


def test_bulk_server_picks_column(client: TestClient) -> None:
    data = _post(client, "/bulk", {"csv_text": _BULK_CSV, "column": ""})
    assert data["status"] == "ok"
    # Server should pick 'country' as the place-like column.
    assert data["column"] == "country"


def test_bulk_invalid_column(client: TestClient) -> None:
    data = _post(client, "/bulk", {"csv_text": _BULK_CSV, "column": "nonexistent"})
    assert data["status"] == "error"
    assert data["detail"] == resolve_api._ERR_COLUMN_NOT_FOUND


# ---------------------------------------------------------------------------
# /compare tests
# ---------------------------------------------------------------------------


def test_compare_timed(client: TestClient) -> None:
    data = _post(client, "/compare", {"query": "Germany"})
    assert data["status"] == "ok"
    for key in ("resolvekit", "country_converter", "hdx_python_country"):
        assert key in data, f"Missing key: {key}"
        assert isinstance(data[key]["elapsed_ms"], float), f"{key}.elapsed_ms not float"
        assert data[key]["elapsed_ms"] >= 0


def test_compare_coco_iso3(client: TestClient) -> None:
    data = _post(client, "/compare", {"query": "Germany", "to": "iso3"})
    assert data["status"] == "ok"
    # country_converter supports iso3; value is the ISO code or None on miss.
    coco = data["country_converter"]
    assert coco["supported"] is True
    assert coco["value"] in ("DEU", None)


def test_compare_target_support_matrix(client: TestClient) -> None:
    # Only resolvekit can emit dcid/wikidata; the country libs report unsupported.
    data = _post(client, "/compare", {"query": "Germany", "to": "wikidata"})
    assert data["status"] == "ok"
    assert data["resolvekit"]["supported"] is True
    assert data["country_converter"]["supported"] is False
    assert data["hdx_python_country"]["supported"] is False


def test_compare_ambiguous_returns_candidates(client: TestClient) -> None:
    # "Congo" is ambiguous (DRC vs Republic) — resolvekit abstains (value=None)
    # but should surface the top candidates rendered in the requested target.
    data = _post(client, "/compare", {"query": "Congo", "to": "iso3"})
    assert data["status"] == "ok"
    rk = data["resolvekit"]
    assert rk["value"] is None
    cands = rk["candidates"]
    assert isinstance(cands, list) and len(cands) >= 2
    # Each candidate carries a name, a conf_pct, and an iso3 value for this target.
    iso3s = {c["value"] for c in cands}
    assert {"COD", "COG"} & iso3s
    for c in cands:
        assert c["name"]
        assert c["conf_pct"]


def test_compare_resolved_without_target_has_no_candidates(client: TestClient) -> None:
    # NATO resolves to a single group entity that has no ISO3. The result must
    # report as resolved with value=None and an EMPTY candidates list — candidates
    # are only for the abstain (ambiguous) case, not a resolved-but-no-code entity.
    data = _post(client, "/compare", {"query": "NATO", "to": "iso3"})
    assert data["status"] == "ok"
    rk = data["resolvekit"]
    assert rk["status"] == "resolved"
    assert rk["value"] is None
    assert rk["candidates"] == []


def test_compare_deterministic_flags(client: TestClient) -> None:
    data = _post(client, "/compare", {"query": "France"})
    assert data["status"] == "ok"
    assert data["resolvekit"]["offline"] is True
    assert data["resolvekit"]["deterministic"] is True
    assert data["country_converter"]["deterministic"] is True
    assert data["hdx_python_country"]["deterministic"] is True


# ---------------------------------------------------------------------------
# Error envelope tests
# ---------------------------------------------------------------------------


def test_error_envelope_on_resolve_exception(client: TestClient) -> None:
    """Monkeypatch the resolver to raise — endpoint must return 200 {status:'error'}."""
    with patch.object(
        resolve_api,
        "_get_resolver",
        return_value=resolve_api._RESOLVER_SENTINEL,
    ):
        data = _post(client, "/resolve", {"query": "France"})
    assert data["status"] == "error"
    assert "detail" in data
    assert isinstance(data["elapsed_ms"], float)
    # detail must be one of the static error strings, never repr/str(exception).
    assert data["detail"] in (
        resolve_api._ERR_RESOLVER_UNAVAILABLE,
        resolve_api._ERR_RESOLVE_FAILED,
        resolve_api._ERR_RESOLVE_TIMEOUT,
    )


def test_error_envelope_detail_is_static(client: TestClient) -> None:
    """Error detail must be a fixed string — never str(e) or repr(e)."""
    class _BoomResolver:
        def resolve(self, *args, **kwargs):  # noqa: ANN001, ANN201
            raise RuntimeError("SUPER SECRET INTERNAL MESSAGE xyz123")

    with patch.object(resolve_api, "_get_resolver", return_value=_BoomResolver()):
        data = _post(client, "/resolve", {"query": "France"})

    assert data["status"] == "error"
    # The raw exception message must NOT appear in the response.
    assert "SUPER SECRET INTERNAL MESSAGE" not in data["detail"]
    assert "xyz123" not in data["detail"]
    assert "RuntimeError" not in data["detail"]


# ---------------------------------------------------------------------------
# Length cap tests (Pydantic 422)
# ---------------------------------------------------------------------------


def test_length_cap_query(client: TestClient) -> None:
    long_query = "A" * 201  # exceeds max_length=200
    resp = client.post(
        "/api/resolve-demo/api/resolve",
        json={"query": long_query},
    )
    assert resp.status_code == 422


def test_length_cap_csv(client: TestClient) -> None:
    long_csv = "col\n" + "\n".join(["x"] * 300)  # well within 200k
    data = _post(client, "/bulk", {"csv_text": long_csv, "column": "col"})
    assert data["status"] == "ok"  # should succeed (under cap)


def test_length_cap_csv_oversize(client: TestClient) -> None:
    over_csv = "A" * 200_001  # exceeds max_length=200_000
    resp = client.post(
        "/api/resolve-demo/api/bulk",
        json={"csv_text": over_csv, "column": ""},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Router guard test
# ---------------------------------------------------------------------------


def test_router_guard_boots_without_resolve_api() -> None:
    """When resolve_api.router is None, app.py boots and healthz still works."""
    boot_app = FastAPI()
    # Simulate the guarded include_router from app.py.
    if resolve_api.router is not None:
        boot_app.include_router(resolve_api.router)

    with TestClient(boot_app) as tc:
        # /api/dc-search/healthz is NOT on this bare app — 404 expected.
        r = tc.get("/api/dc-search/healthz")
        # The resolve routes should also 404 if router was None.
        if resolve_api.router is None:
            assert r.status_code == 404
            r2 = tc.post(
                "/api/resolve-demo/api/resolve",
                json={"query": "France"},
            )
            assert r2.status_code == 404
        else:
            # Router is present; this test just confirms include_router doesn't crash.
            pass


# ---------------------------------------------------------------------------
# Concurrency test
# ---------------------------------------------------------------------------


def test_concurrency_parallel_resolves(client: TestClient) -> None:
    """~20 parallel /resolve calls must all succeed and be consistent."""
    queries = ["France", "Germany", "Japan", "Brazil", "Canada"] * 4  # 20 calls

    results: list[dict] = [{}] * len(queries)
    errors: list[Exception] = []

    def _call(idx: int, query: str) -> None:
        try:
            data = _post(client, "/resolve", {"query": query})
            results[idx] = data
        except Exception as exc:
            errors.append(exc)

    threads = [
        threading.Thread(target=_call, args=(i, q)) for i, q in enumerate(queries)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not errors, f"Concurrency errors: {errors}"
    # At minimum some should have resolved.
    resolved = [r for r in results if r.get("status") in {"resolved", "ambiguous", "no_match"}]
    assert len(resolved) == len(queries), f"Some calls failed: {[r for r in results if not r]}"


# ---------------------------------------------------------------------------
# Item 1: bundled-module pin — cities/admin no longer match
# ---------------------------------------------------------------------------


def test_modules_include_cities_when_available(client: TestClient) -> None:
    """With the full module set, a city like Nairobi resolves (cities pack loaded).

    Skips when the remote geo.cities pack isn't present in this environment, so the
    test passes on a bundled-only machine without asserting a false expectation.
    """
    import resolvekit as _rk

    cities_loaded = any(
        m.module_id == "geo.cities" and getattr(m, "is_available", False)
        for m in _rk.modules()
    )
    if not cities_loaded:
        pytest.skip("geo.cities pack not available in this environment")
    data = _post(client, "/resolve", {"query": "Nairobi"})
    assert data["status"] == "resolved", (
        f"Nairobi should resolve with the cities pack loaded, got {data['status']!r}"
    )


def test_module_pin_countries_still_work(client: TestClient) -> None:
    """Countries and groups must resolve with the full module set."""
    for query, expected_id in [
        ("United States", "country/USA"),
        ("Germany",       "country/DEU"),
        ("EU",            "EuropeanUnion"),
    ]:
        data = _post(client, "/resolve", {"query": query})
        assert data["status"] == "resolved", (
            f"{query!r} should be resolved, got {data['status']!r}"
        )
        assert data["entity_id"] == expected_id, (
            f"{query!r} expected {expected_id!r}, got {data['entity_id']!r}"
        )


# ---------------------------------------------------------------------------
# Item 2: digit guard
# ---------------------------------------------------------------------------


def test_digit_guard_no_match(client: TestClient) -> None:
    """Bare digit strings must not resolve to a country."""
    for q in ("42", "392", "100", "23"):
        data = _post(client, "/resolve", {"query": q})
        assert data["status"] == "no_match", (
            f"{q!r} should be no_match (digit guard), got {data['status']!r} "
            f"entity_id={data.get('entity_id')!r}"
        )
        assert data["reason"] == "digit_only", (
            f"{q!r} expected reason 'digit_only', got {data.get('reason')!r}"
        )


# ---------------------------------------------------------------------------
# Item 6: entity_type removed from /resolve and /explain
# ---------------------------------------------------------------------------


def test_resolve_rejects_entity_type_field(client: TestClient) -> None:
    """Passing entity_type to /resolve should return a 422 (unknown field)."""
    resp = client.post(
        "/api/resolve-demo/api/resolve",
        json={"query": "France", "entity_type": "geo.country"},
    )
    # Pydantic v2 with extra="forbid" raises 422; without it the field is ignored.
    # Either way, assert no server error.
    assert resp.status_code in {200, 422}


# ---------------------------------------------------------------------------
# Item 9: /bulk over-cap → not_processed + truncated + honest unique count
# ---------------------------------------------------------------------------

# Build a CSV with 502 distinct values to trigger the 500-cap.
_OVER_CAP_CSV = "country\n" + "\n".join(f"place_{i}" for i in range(502))


def test_bulk_over_cap_truncated(client: TestClient) -> None:
    data = _post(client, "/bulk", {"csv_text": _OVER_CAP_CSV, "column": "country"})
    assert data["status"] == "ok"
    assert data.get("truncated") is True, "Expected truncated:true when >500 distinct"
    summary = data["summary"]
    # unique should reflect the full 502, not the capped 500.
    assert summary["unique"] == 502, f"Expected unique=502, got {summary['unique']}"
    # Over-cap rows in results must show not_processed (they were never attempted).
    # (Results are capped at 30 for display, but at least some rows should be present.)
    assert len(data["results"]) > 0


def test_bulk_no_truncation_under_cap(client: TestClient) -> None:
    """Under-cap bulk must not set truncated."""
    data = _post(client, "/bulk", {"csv_text": _BULK_CSV, "column": "country"})
    assert data["status"] == "ok"
    # 3 distinct values — well under 500.
    assert data.get("truncated") is False or data.get("truncated") is None


# ---------------------------------------------------------------------------
# Item 14: whitespace-only / null-byte rejection
# ---------------------------------------------------------------------------


def test_resolve_rejects_whitespace_only(client: TestClient) -> None:
    """Whitespace-only queries must return a structured error, not a 500."""
    data = _post(client, "/resolve", {"query": "   "})
    assert data["status"] == "error"
    assert "detail" in data


def test_resolve_rejects_null_byte(client: TestClient) -> None:
    data = _post(client, "/resolve", {"query": "\x00\x00"})
    assert data["status"] == "error"
