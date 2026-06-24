"""E2E engine tests: candidates production trigger.

Tests the standard-family goldens that must return status=candidates, plus std-06b
(definite via the dominance rule) and a df-09 canary that must stay definite. cand-r2
is deferred (skipped) — see _SUBNATIONAL_GEO_GAP.

All tests run fully offline via the shared offline_resolve harness (FakeGraph +
FakeLLM + pinned fixtures).

Goldens covered:
  std-02   GDP India                          -> candidates, 2..6 specs
  std-03   birth rate Ethiopia                -> candidates, 2..6 specs
  std-04b  number of births in Ethiopia       -> candidates, 2..6 specs
  std-05   under-5 child mortality rate       -> candidates, 2..6 specs (no entity)
  std-06b  fertility rate in Kenya            -> definite FertilityRate_Person_Female (dominance)
  std-07   infant mortality rate Ethiopia     -> candidates, 2..6 specs
  sdg-06   government education spending Kenya -> candidates, 2..6 specs
  cand-r1  GDP of Brazil                      -> candidates, 2..6 specs
  cand-r2  income in California               -> candidates, exactly 2 specs (DEFERRED/skipped)
  df-09    health ODA from Germany to Ethiopia -> definite (regression guard)

std-05 has no place entity in the query ("under-5 child mortality rate").
The no-entity candidates path skips the per-entity observation probe and
returns bare coverage from SV existence, producing one spec per confirmed
shape for variable disambiguation.
"""
from __future__ import annotations

import pytest

from qre.models import (
    CandidatesResponse,
    DefiniteResponse,
    RawTextInput,
    ResolveRequest,
)
from tests.engine._harness import offline_resolve

# cand-r2 ('income in California') is deferred: the engine resolves Country-typed entities
# only (graph.resolve_entity uses a typeOf:Country filter), so 'California' (geoId/06, a US
# State) fails to resolve and the query returns no_data/entity_not_resolved on EVERY endpoint
# (staging and prod alike) — not a fixture problem. A trace confirmed both endpoints behave
# identically. Deferred until the engine handles sub-national geo entities (the fix is to fall
# back to detect's own entity resolution in recall(), which already returns geoId/06).
# cand-r1 ('GDP of Brazil') was previously skipped under the same theory but is NOT a gap: it
# resolves to candidates on staging and prod. Its earlier all-attempts failure was a transient
# staging-capacity blip on the node endpoint; re-recorded with the 0.3s recorder throttle.
_SUBNATIONAL_GEO_GAP = (
    "deferred: engine resolves Country entities only; California (geoId/06) is a State, "
    "so this returns entity_not_resolved on staging and prod alike (not a fixture issue)"
)


def _req(query: str) -> ResolveRequest:
    return ResolveRequest(input=RawTextInput(query=query))


def _candidates(query: str) -> CandidatesResponse:
    result = offline_resolve(_req(query))
    root = result.root
    assert isinstance(root, CandidatesResponse), (
        f"expected candidates for {query!r}, got status={root.status!r}"
    )
    return root


# ---------------------------------------------------------------------------
# Shared invariant helpers
# ---------------------------------------------------------------------------

def _check_invariants(response: CandidatesResponse, *, query: str, max_count: int = 6) -> None:
    """Assert the three candidates invariants: count, distinct spec_ids, broadest-first."""
    specs = response.candidates.specs
    count = len(specs)

    assert 2 <= count <= max_count, (
        f"{query!r}: expected 2..{max_count} specs, got {count}"
    )

    spec_ids = [s.spec_id for s in specs]
    assert len(spec_ids) == len(set(spec_ids)), (
        f"{query!r}: duplicate spec_ids: {spec_ids}"
    )

    member_counts = [s.shape.member_count for s in specs]
    for i in range(len(member_counts) - 1):
        assert member_counts[i] >= member_counts[i + 1], (
            f"{query!r}: not broadest-first at index {i}: {member_counts}"
        )


# ---------------------------------------------------------------------------
# std-02: GDP India
# ---------------------------------------------------------------------------

class TestStd02GDPIndia:
    """std-02: 'GDP India' -> candidates, 2..6 specs, broadest-first, distinct."""

    _QUERY = "GDP India"

    def test_status_is_candidates(self):
        result = offline_resolve(_req(self._QUERY))
        assert result.root.status == "candidates"

    def test_spec_count_in_range(self):
        resp = _candidates(self._QUERY)
        assert 2 <= len(resp.candidates.specs) <= 6

    def test_spec_ids_are_distinct(self):
        resp = _candidates(self._QUERY)
        ids = [s.spec_id for s in resp.candidates.specs]
        assert len(ids) == len(set(ids))

    def test_order_is_broadest_first(self):
        resp = _candidates(self._QUERY)
        counts = [s.shape.member_count for s in resp.candidates.specs]
        for i in range(len(counts) - 1):
            assert counts[i] >= counts[i + 1], f"not broadest-first: {counts}"


# ---------------------------------------------------------------------------
# std-03: birth rate Ethiopia
# ---------------------------------------------------------------------------

class TestStd03BirthRateEthiopia:
    """std-03: 'birth rate Ethiopia' -> candidates, 2..6 specs."""

    _QUERY = "birth rate Ethiopia"

    def test_status_is_candidates(self):
        assert offline_resolve(_req(self._QUERY)).root.status == "candidates"

    def test_invariants(self):
        _check_invariants(_candidates(self._QUERY), query=self._QUERY)


# ---------------------------------------------------------------------------
# std-04b: number of births in Ethiopia
# ---------------------------------------------------------------------------

class TestStd04bBirthsEthiopia:
    """std-04b: 'number of births in Ethiopia' -> candidates, 2..6 specs."""

    _QUERY = "number of births in Ethiopia"

    def test_status_is_candidates(self):
        assert offline_resolve(_req(self._QUERY)).root.status == "candidates"

    def test_invariants(self):
        _check_invariants(_candidates(self._QUERY), query=self._QUERY)


# ---------------------------------------------------------------------------
# std-05: under-5 child mortality rate — no entity, variable disambiguation
# ---------------------------------------------------------------------------

class TestStd05Under5ChildMortality:
    """std-05: 'under-5 child mortality rate' -> candidates, 2..6 specs.

    No place entity in the query.  The no-entity candidates path skips the
    per-entity observation probe and returns bare coverage (has_data from SV
    existence), producing one spec per confirmed shape for variable
    disambiguation.  The golden notes five structurally distinct shapes confirmed
    live; the offline fixture may produce up to QRE_MAX_CANDIDATES (6) shapes
    from a noisier detect set.
    """

    _QUERY = "under-5 child mortality rate"

    def test_status_is_candidates(self):
        assert offline_resolve(_req(self._QUERY)).root.status == "candidates"

    def test_invariants(self):
        _check_invariants(_candidates(self._QUERY), query=self._QUERY)

    def test_no_entities_in_specs(self):
        """All specs must have empty entity lists (no place was in the query)."""
        resp = _candidates(self._QUERY)
        for spec in resp.candidates.specs:
            assert spec.entities == [], (
                f"expected no entities in std-05 spec {spec.spec_id!r}, "
                f"got {[e.ref.dcid for e in spec.entities]}"
            )

    def test_has_data_bare_coverage(self):
        """Bare coverage is used (no entity probe); has_data must be True."""
        resp = _candidates(self._QUERY)
        for spec in resp.candidates.specs:
            assert spec.coverage.has_data is True, (
                f"expected has_data=True for std-05 spec {spec.spec_id!r}"
            )


# ---------------------------------------------------------------------------
# std-06b: fertility rate in Kenya
# ---------------------------------------------------------------------------

class TestStd06bFertilityRateKenya:
    """std-06b: 'fertility rate in Kenya' -> definite FertilityRate_Person_Female.

    FertilityRate_Person_Female dominates the candidate field (cosine margin 0.243,
    the strongest in the standard set), so the dominance rule resolves it definite
    rather than listing the fertility sub-variants. The margin exceeds std-01's,
    so any threshold that makes std-01 definite makes this definite too.
    """

    _QUERY = "fertility rate in Kenya"

    def test_status_is_definite(self):
        assert offline_resolve(_req(self._QUERY)).root.status == "definite"

    def test_sv_is_fertility_rate(self):
        inner = offline_resolve(_req(self._QUERY)).root
        assert isinstance(inner, DefiniteResponse)
        sv_dcids = [sv.ref.dcid for sv in inner.interpretation.stat_vars]
        assert "FertilityRate_Person_Female" in sv_dcids


# ---------------------------------------------------------------------------
# std-07: infant mortality rate Ethiopia
# ---------------------------------------------------------------------------

class TestStd07InfantMortalityEthiopia:
    """std-07: 'infant mortality rate Ethiopia' -> candidates, 2..6 specs."""

    _QUERY = "infant mortality rate Ethiopia"

    def test_status_is_candidates(self):
        assert offline_resolve(_req(self._QUERY)).root.status == "candidates"

    def test_invariants(self):
        _check_invariants(_candidates(self._QUERY), query=self._QUERY)


# ---------------------------------------------------------------------------
# sdg-06: government education spending Kenya
# ---------------------------------------------------------------------------

class TestSdg06EducationSpendingKenya:
    """sdg-06: 'government education spending Kenya' -> candidates, 2..6 specs."""

    _QUERY = "government education spending Kenya"

    def test_status_is_candidates(self):
        assert offline_resolve(_req(self._QUERY)).root.status == "candidates"

    def test_invariants(self):
        _check_invariants(_candidates(self._QUERY), query=self._QUERY)


# ---------------------------------------------------------------------------
# cand-r1: GDP of Brazil
# ---------------------------------------------------------------------------

class TestCandR1GDPBrazil:
    """cand-r1: 'GDP of Brazil' -> candidates, 2..6 specs.

    Engine returns 3 specs (the golden's 4th SV, ONE/who_gge-gdp, has
    measuredProperty=null and is correctly dropped before the candidates decision),
    which the 2..6 invariant covers.
    """

    _QUERY = "GDP of Brazil"

    def test_status_is_candidates(self):
        assert offline_resolve(_req(self._QUERY)).root.status == "candidates"

    def test_invariants(self):
        _check_invariants(_candidates(self._QUERY), query=self._QUERY)


# ---------------------------------------------------------------------------
# cand-r2: income in California -> exactly 2 candidates
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason=_SUBNATIONAL_GEO_GAP)
class TestCandR2IncomeCaliforniaExactly2:
    """cand-r2: 'income in California' -> exactly 2 candidates."""

    _QUERY = "income in California"

    def test_status_is_candidates(self):
        assert offline_resolve(_req(self._QUERY)).root.status == "candidates"

    def test_exactly_2_specs(self):
        resp = _candidates(self._QUERY)
        assert len(resp.candidates.specs) == 2, (
            f"expected exactly 2 specs, got {len(resp.candidates.specs)}"
        )

    def test_spec_ids_are_distinct(self):
        resp = _candidates(self._QUERY)
        ids = [s.spec_id for s in resp.candidates.specs]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# df-09 canary: health ODA from Germany to Ethiopia must stay definite
# ---------------------------------------------------------------------------

class TestDf09CanaryStaysDefinite:
    """df-09 regression guard: dev-finance query must not be captured by candidates path."""

    _QUERY = "health official development assistance from Germany to Ethiopia"

    def test_status_is_definite(self):
        result = offline_resolve(_req(self._QUERY))
        assert result.root.status == "definite", (
            f"df-09 regressed: expected definite, got {result.root.status!r}"
        )

    def test_shape_is_dev_finance(self):
        result = offline_resolve(_req(self._QUERY))
        assert isinstance(result.root, DefiniteResponse)
        # dev-finance shapes use the dev_finance_crs_dac shape_id
        assert result.root.interpretation.shape.shape_id == "dev_finance_crs_dac"
