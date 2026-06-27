"""End-to-end rendered_sentence tests for resolve(..., include_sentence=True).

Each test asserts the EXACT rendered_sentence produced by the offline harness.
Sentences were captured 2026-06-27 and must stay stable; a drift means the render
layer or fixture labels changed and both need to be audited together.

Gate: all 8 cases must pass before the faithful donor read-back feature ships.
"""
from __future__ import annotations

from qre.models import (
    CandidatesResponse,
    DefiniteResponse,
    NoDataResponse,
    RawTextInput,
    ResolveOptions,
    ResolveRequest,
)
from qre.render import no_data_phrase
from tests.engine._harness import offline_resolve


def _resolve(
    query: str, *, pac: bool | None = None
) -> DefiniteResponse | CandidatesResponse | NoDataResponse:
    opts = (
        ResolveOptions(include_sentence=True, place_as_constraint=pac)
        if pac is not None
        else ResolveOptions(include_sentence=True)
    )
    return offline_resolve(
        ResolveRequest(input=RawTextInput(query=query), options=opts)
    ).root


# ---------------------------------------------------------------------------
# Case 1: donor + recipient both voiced (headline gate)
# ---------------------------------------------------------------------------


def test_donor_and_recipient_sentence():
    """Definite: both donor (from) and recipient (to) are voiced in the sentence."""
    r = _resolve("health ODA grants from USA to Ethiopia")
    assert isinstance(r, DefiniteResponse)
    assert r.rendered_sentence == (
        "Official Development Assistance Grants for Health (Total) to Ethiopia from United States."
    )
    # Faithful donor read-back: USA carries the "from" directional role.
    roles = {e.ref.dcid: e.role for e in r.interpretation.entities}
    assert roles["country/USA"].direction == "from"
    assert roles["country/USA"].role.dcid == "observationAbout"


# ---------------------------------------------------------------------------
# Case 2: recipient only — no spurious "from"
# ---------------------------------------------------------------------------


def test_recipient_only_sentence():
    """Definite: recipient voiced as "to"; no donor means no "from" in sentence."""
    r = _resolve("health ODA grants to Ethiopia")
    assert isinstance(r, DefiniteResponse)
    assert r.rendered_sentence == (
        "Official Development Assistance Grants for Health (Total) to Ethiopia."
    )
    assert " from " not in r.rendered_sentence


# ---------------------------------------------------------------------------
# Case 3: place_as_constraint=False — all entities become subjects
# ---------------------------------------------------------------------------


def test_place_as_constraint_false_sentence():
    """pac=False: "in Ethiopia" (subject); no directional voiced; ENTITY_ROLE_DISABLED warned."""
    r = _resolve("health ODA grants from USA to Ethiopia", pac=False)
    assert isinstance(r, DefiniteResponse)
    assert r.rendered_sentence == (
        "Official Development Assistance Grants for Health (Total) in Ethiopia."
    )
    assert " to " not in r.rendered_sentence
    assert " from " not in r.rendered_sentence
    # All entities must be subjects when the seam is off.
    assert all(e.role.kind == "subject" for e in r.interpretation.entities)
    # The seam-off warning must be present.
    assert "ENTITY_ROLE_DISABLED" in {w.code for w in r.diagnostics.warnings}


# ---------------------------------------------------------------------------
# Case 4: no_data — variable not resolved
# ---------------------------------------------------------------------------


def test_donor_only_no_data_sentence():
    """Donor without a recipient yields no_data / variable_not_resolved."""
    r = _resolve("health ODA grants from USA")
    assert isinstance(r, NoDataResponse)
    assert r.rendered_sentence == no_data_phrase("variable_not_resolved")


# ---------------------------------------------------------------------------
# Case 5: standard place — no spurious directional voice
# ---------------------------------------------------------------------------


def test_standard_place_population_sentence():
    """Definite standard SV: bare subject entity; no "from" or "to" in sentence."""
    r = _resolve("total population India")
    assert isinstance(r, DefiniteResponse)
    assert r.rendered_sentence == "Count."
    assert " from " not in r.rendered_sentence
    assert " to " not in r.rendered_sentence


# ---------------------------------------------------------------------------
# Case 6: standard place with how-qualifier — no spurious directional voice
# ---------------------------------------------------------------------------


def test_standard_place_fertility_sentence():
    """Definite standard SV with how-qualifier; no "from" or "to" in sentence."""
    r = _resolve("fertility rate in Kenya")
    assert isinstance(r, DefiniteResponse)
    assert r.rendered_sentence == "FertilityRate for Female."
    assert " from " not in r.rendered_sentence
    assert " to " not in r.rendered_sentence


# ---------------------------------------------------------------------------
# Case 7: candidates — count summary with trailing period
# ---------------------------------------------------------------------------


def test_candidates_count_summary_sentence():
    """Candidates: rendered_sentence is the count summary ending with a period."""
    r = _resolve("government education spending Kenya")
    assert isinstance(r, CandidatesResponse)
    assert r.rendered_sentence == "3 possible interpretations."


# ---------------------------------------------------------------------------
# Case 8: no_data — denominator not available
# ---------------------------------------------------------------------------


def test_per_capita_no_denominator_sentence():
    """Per-capita query with no denominator yields the dedicated no_data phrase."""
    r = _resolve("health ODA per capita to Ethiopia")
    assert isinstance(r, NoDataResponse)
    assert r.rendered_sentence == "No denominator is available for this per-capita query."


# ---------------------------------------------------------------------------
# Case 9: bare dev-finance entity (no directional preposition) — the entity is
# voiced via its where slot as "in <place>", never as a spurious "from" donor.
# This pins the donor-detection-by-direction side effect: with no "from"/"to",
# donor_dcid stays None and the entity resolves as the recipient.
# ---------------------------------------------------------------------------


def test_bare_dev_finance_entity_no_spurious_from_sentence():
    """Bare dev-finance entity: voiced as "in Kenya", with no directional donor."""
    r = _resolve("health ODA grants Kenya")
    assert isinstance(r, DefiniteResponse)
    assert r.rendered_sentence == (
        "Official Development Assistance Grants for Health (Total) in Kenya."
    )
    assert " from " not in r.rendered_sentence
