"""Tests for place_role.py: directional_roles.

Verifies role assignment, seam ON/OFF behavior, invariants, and warning flags.
"""
from __future__ import annotations

from qre.engine.place_role import (
    DirectionalRole,
    EntityRoleDraft,
    SubjectRole,
    directional_roles,
)

RECIPIENT_ROLE_DCID = "DevelopmentFinanceRecipient"
DONOR_ROLE_DCID = "observationAbout"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _roles(
    query: str,
    entities: list[tuple[str, str | None]],
    *,
    seam_on: bool = True,
) -> dict[str, EntityRoleDraft]:
    roles, _, _ = directional_roles(
        query,
        entities,
        place_as_constraint=seam_on,
        recipient_role_dcid=RECIPIENT_ROLE_DCID,
        donor_role_dcid=DONOR_ROLE_DCID,
    )
    return roles


def _roles_with_flags(
    query: str,
    entities: list[tuple[str, str | None]],
    *,
    seam_on: bool = True,
) -> tuple[dict[str, EntityRoleDraft], bool, bool]:
    """Return (roles, seam_off, directional_detected) for warning-flag assertions."""
    return directional_roles(
        query,
        entities,
        place_as_constraint=seam_on,
        recipient_role_dcid=RECIPIENT_ROLE_DCID,
        donor_role_dcid=DONOR_ROLE_DCID,
    )




def test_from_donor_to_recipient() -> None:
    """'from USA to Ethiopia' → USA=directional/from, ETH=directional/to."""
    result = _roles(
        "health ODA grants from USA to Ethiopia",
        [("country/USA", "USA"), ("country/ETH", "Ethiopia")],
        seam_on=True,
    )
    assert "country/USA" in result
    assert "country/ETH" in result

    usa_role = result["country/USA"].role
    eth_role = result["country/ETH"].role

    assert isinstance(usa_role, DirectionalRole)
    assert usa_role.direction == "from"
    assert usa_role.role_dcid == DONOR_ROLE_DCID
    assert isinstance(eth_role, DirectionalRole)
    assert eth_role.direction == "to"
    assert eth_role.role_dcid == RECIPIENT_ROLE_DCID


def test_from_donor_is_directional_from() -> None:
    """The donor (from) is DirectionalRole with direction=from, not a SubjectRole."""
    result = _roles(
        "ODA from USA to Kenya",
        [("country/USA", "USA"), ("country/KEN", "Kenya")],
        seam_on=True,
    )
    usa_role = result["country/USA"].role
    assert isinstance(usa_role, DirectionalRole)
    assert usa_role.direction == "from"
    assert usa_role.role_dcid == DONOR_ROLE_DCID




def test_to_only_recipient_only() -> None:
    """'ODA to Ethiopia' → ETH=directional/to; no donor entity in list."""
    result = _roles(
        "health aid to Ethiopia",
        [("country/ETH", "Ethiopia")],
        seam_on=True,
    )
    assert len(result) == 1
    eth_role = result["country/ETH"].role
    assert isinstance(eth_role, DirectionalRole)
    assert eth_role.direction == "to"


def test_to_only_returns_correct_role_dcid() -> None:
    result = _roles(
        "ODA flows to Kenya",
        [("country/KEN", "Kenya")],
        seam_on=True,
    )
    assert result["country/KEN"].role.role_dcid == RECIPIENT_ROLE_DCID  # type: ignore[union-attr]




def test_ambiguous_entity_becomes_subject() -> None:
    """Entity mentioned without directional preposition → subject (fail-open)."""
    result = _roles(
        "health ODA Ethiopia",  # no "from" or "to" near Ethiopia
        [("country/ETH", "Ethiopia")],
        seam_on=True,
    )
    assert isinstance(result["country/ETH"].role, SubjectRole)




def test_seam_off_all_subjects() -> None:
    """Seam OFF → every entity is EntityRoleSubject regardless of prepositions."""
    result = _roles(
        "health ODA from USA to Ethiopia",
        [("country/USA", "USA"), ("country/ETH", "Ethiopia")],
        seam_on=False,
    )
    for dcid, draft in result.items():
        assert isinstance(draft.role, SubjectRole), (
            f"{dcid} should be subject when seam is OFF"
        )


def test_seam_off_warning_should_warn_seam_off() -> None:
    _, seam_off, _ = _roles_with_flags(
        "health ODA from USA to Ethiopia",
        [("country/USA", "USA"), ("country/ETH", "Ethiopia")],
        seam_on=False,
    )
    assert seam_off is True


def test_seam_off_warning_role_disabled_when_directional_detected() -> None:
    """When seam is OFF and direction was detected, ENTITY_ROLE_DISABLED should be emitted."""
    _, seam_off, directional_detected = _roles_with_flags(
        "ODA from USA to Ethiopia",
        [("country/USA", "USA"), ("country/ETH", "Ethiopia")],
        seam_on=False,
    )
    assert seam_off and directional_detected


def test_seam_off_no_role_disabled_when_no_direction_detected() -> None:
    """Seam OFF but no directional prepositions → ENTITY_ROLE_DISABLED should NOT fire."""
    _, seam_off, directional_detected = _roles_with_flags(
        "health ODA Ethiopia",  # no from/to
        [("country/ETH", "Ethiopia")],
        seam_on=False,
    )
    # seam_off is True but directional_detected is False
    assert seam_off is True
    assert directional_detected is False


def test_seam_on_no_warnings() -> None:
    """Seam ON → seam_off is False, so no warnings should fire."""
    _, seam_off, directional_detected = _roles_with_flags(
        "ODA from USA to Ethiopia",
        [("country/USA", "USA"), ("country/ETH", "Ethiopia")],
        seam_on=True,
    )
    assert seam_off is False
    # With seam ON, role_disabled = seam_off and directional_detected = False regardless
    assert not (seam_off and directional_detected)




def test_noop_invariant_same_dcid_set() -> None:
    """Seam ON vs OFF must return the same set of dcid keys."""
    entities = [("country/USA", "USA"), ("country/ETH", "Ethiopia")]
    query = "health ODA grants from USA to Ethiopia"

    result_on = _roles(query, entities, seam_on=True)
    result_off = _roles(query, entities, seam_on=False)

    assert set(result_on.keys()) == set(result_off.keys())


def test_noop_invariant_graphref_dcids_identical() -> None:
    """Entity dcids in the result are the same regardless of seam."""
    entities = [("country/GBR", "UK"), ("country/KEN", "Kenya")]
    query = "ODA grants from UK to Kenya"

    on_dcids = set(_roles(query, entities, seam_on=True).keys())
    off_dcids = set(_roles(query, entities, seam_on=False).keys())
    assert on_dcids == off_dcids




def test_multiple_entities_correct_roles() -> None:
    """Three entities; one donor, one recipient, one ambiguous."""
    entities = [
        ("country/USA", "USA"),
        ("country/ETH", "Ethiopia"),
        ("country/KEN", "Kenya"),  # appears ambiguously in the query
    ]
    query = "ODA from USA to Ethiopia and Kenya health"
    result = _roles(query, entities, seam_on=True)

    assert len(result) == 3
    # USA is donor → directional/from
    usa_role = result["country/USA"].role
    assert isinstance(usa_role, DirectionalRole)
    assert usa_role.direction == "from"
    assert usa_role.role_dcid == DONOR_ROLE_DCID
    # ETH is recipient → directional/to
    assert isinstance(result["country/ETH"].role, DirectionalRole)
    assert result["country/ETH"].role.direction == "to"




def test_dcid_segment_fallback_when_no_surface() -> None:
    """When surface is None, the DCID last segment is used as fallback anchor."""
    # Query mentions "ETH" (the ISO3 code, last segment of country/ETH)
    result = _roles(
        "ODA to ETH",
        [("country/ETH", None)],  # no surface text
        seam_on=True,
    )
    assert isinstance(result["country/ETH"].role, DirectionalRole)
    assert result["country/ETH"].role.direction == "to"




def test_empty_entities_returns_empty_dict() -> None:
    result = _roles("ODA from USA to Ethiopia", [], seam_on=True)
    assert dict(result) == {}


def test_empty_entities_seam_off_no_warnings() -> None:
    _, seam_off, directional_detected = _roles_with_flags(
        "ODA from USA to Ethiopia", [], seam_on=False
    )
    assert seam_off is True
    # No direction can be detected if no entities → directional_detected False
    assert directional_detected is False
