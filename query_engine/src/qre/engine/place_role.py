"""Place-role classification for the QRE engine.

Decides whether a resolved entity is a donor (from → subject) or recipient
(to → directional) based on directional prepositions in the raw query.

Seam flag:
  place_as_constraint=True (default): recipients become EntityRoleDirectional,
    donors EntityRoleSubject.
  place_as_constraint=False: all entities become EntityRoleSubject; a warning is
    emitted when directionality is detected.

Pure module: no LLM, no graph calls.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class SubjectRole:
    """Entity plays the subject role (default)."""
    kind: Literal["subject"] = "subject"


@dataclass(frozen=True)
class DirectionalRole:
    """Entity plays a directional endpoint role (to/from) in a flow."""
    kind: Literal["directional"] = "directional"
    direction: Literal["from", "to"] = "to"
    role_dcid: str = ""  # dcid of the role node (e.g. "DevelopmentFinanceRecipient")


RoleDraft = SubjectRole | DirectionalRole


@dataclass
class EntityRoleDraft:
    """Role assignment for one resolved entity."""
    dcid: str
    surface: str | None  # the verbatim query text used to name it, if known
    role: RoleDraft


# Warning codes emitted when the seam is off but directionality was detected.
SEAM_OFF_INFO_CODE = "PLACE_CONSTRAINT_SEAM_OFF"
SEAM_OFF_WARN_CODE = "ENTITY_ROLE_DISABLED"


# Tokenizer constants
_ADJACENCY_WORDS = 3
_DONOR_PREPS: frozenset[str] = frozenset({"from"})
_RECIPIENT_PREPS: frozenset[str] = frozenset({"to"})
_TOKEN_RE = re.compile(r"[a-z][a-z'\-]*[a-z]|[a-z]")


def _tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def _detect_direction(
    query: str,
    surface: str | None,
    dcid: str,
) -> Literal["from", "to"] | None:
    """Return "from", "to", or None (ambiguous) for one entity.

    Tries the verbatim surface text first, then the DCID's last path segment,
    as anchor phrases. For the first anchor found in the query, checks the
    preceding window for a directional preposition.
    """
    last_segment = dcid.rsplit("/", 1)[-1]
    raw_anchors: list[str | None] = [surface, last_segment]

    anchors: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for raw in raw_anchors:
        if not raw:
            continue
        tokens = _tokenize(raw)
        key = tuple(tokens)
        if tokens and key not in seen:
            anchors.append(tokens)
            seen.add(key)

    if not anchors:
        return None

    query_tokens = _tokenize(query)
    n = len(query_tokens)

    for anchor_tokens in anchors:
        anchor_len = len(anchor_tokens)
        for i in range(n - anchor_len + 1):
            if query_tokens[i: i + anchor_len] != anchor_tokens:
                continue
            window_start = max(0, i - _ADJACENCY_WORDS)
            window = query_tokens[window_start:i]
            for prep in reversed(window):  # nearest preposition wins
                if prep in _DONOR_PREPS:
                    return "from"
                if prep in _RECIPIENT_PREPS:
                    return "to"
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def directional_roles(
    query: str,
    resolved_entities: list[tuple[str, str | None]],
    *,
    place_as_constraint: bool,
    recipient_role_dcid: str,
) -> tuple[dict[str, EntityRoleDraft], bool, bool]:
    """Assign directional roles to a list of resolved entities.

    Args:
        query: The raw user query string (used for preposition scanning).
        resolved_entities: List of (dcid, surface) pairs. surface is the
            verbatim query text used to name the entity, or None.
        place_as_constraint: When True (server default), recipients (detected
            via "to <entity>") receive EntityRoleDirectional; donors receive
            EntityRoleSubject. When False, all entities receive EntityRoleSubject
            regardless of detected directionality.
        recipient_role_dcid: The dcid for the directional role node (e.g.
            "DevelopmentFinanceRecipient"). Used only when place_as_constraint=True.

    Returns:
        3-tuple of (roles, seam_off, directional_detected):
          - roles: dict[dcid, EntityRoleDraft] with one entry per input entity.
          - seam_off: True when place_as_constraint is False.
          - directional_detected: True when any entity had a directional preposition.

    No-op invariant: the set of dcid keys is identical regardless of the
    place_as_constraint flag value.
    """
    result: dict[str, EntityRoleDraft] = {}
    detected_any_direction = False

    if place_as_constraint:
        for dcid, surface in resolved_entities:
            direction = _detect_direction(query, surface, dcid)
            if direction == "to":
                detected_any_direction = True
                role: RoleDraft = DirectionalRole(
                    kind="directional",
                    direction="to",
                    role_dcid=recipient_role_dcid,
                )
            elif direction == "from":
                detected_any_direction = True
                role = SubjectRole()
            else:
                # Ambiguous — default to subject (fail-open)
                role = SubjectRole()
            result[dcid] = EntityRoleDraft(dcid=dcid, surface=surface, role=role)
    else:
        for dcid, surface in resolved_entities:
            direction = _detect_direction(query, surface, dcid)
            if direction is not None:
                detected_any_direction = True
            result[dcid] = EntityRoleDraft(
                dcid=dcid, surface=surface, role=SubjectRole()
            )

    return result, not place_as_constraint, detected_any_direction
