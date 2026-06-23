"""Axis classification: AXIS_OVERRIDES literal and the place-fraction auto-rule.

AXIS_OVERRIDES is the single source of truth for hand-pinned property→axis mappings.
See .design/graph-grounding.md for the full measurement basis.

The auto-rule classifies a constraint property to an axis from the DCID-namespace
shape of its observed values:
  - place-DCID namespace fraction >= 0.9 → "where"
  - date-shaped values → "when"
  - everything else → "how"

The override map pins the rule-breakers so a corpus shift cannot silently reclassify
them. DevelopmentFinanceRecipient and DevelopmentFinanceScheme must be overridden
to prevent misclassification.

Pure module: no I/O, no imports from qre.engine.* (only stdlib).
"""
from __future__ import annotations

from typing import Literal

Axis = Literal["what", "how", "where", "when"]

# Override map: hand-pinned property→axis mappings.
AXIS_OVERRIDES: dict[str, Axis] = {
    "comparisonRegion": "where",

    # dev-finance custom overrides: prevent misclassification
    "DevelopmentFinanceRecipient": "where",
    "DevelopmentFinanceScheme": "what",

    # place-named props that take categorical enums, not geo DCIDs
    "placeOfBirth": "how",
    "placeOfResidenceClassification": "how",
    "placeOfWork": "how",
    "placeCategory": "how",
    "locationType": "how",
    "jurisdiction": "how",
    "computerUsageLocation": "how",
    "internetUsageLocation": "how",

    # time-named props that take duration/epoch-bucket enums, not ISO dates
    "dateBuilt": "how",
    "periodOfMilitaryService": "how",
    "instrumentTerm": "how",
    "maturity": "how",
    "commuteTime": "how",
    "accumulationPeriod": "how",
    "extremesOverTime": "how",
}

# ---------------------------------------------------------------------------
# Place-namespace set (used by the auto-rule)
# ---------------------------------------------------------------------------

_PLACE_NAMESPACES: frozenset[str] = frozenset({
    "country", "geoId", "nuts", "wikidataId", "Earth", "continent", "undata-geo",
})

# Simple ISO-date heuristic: YYYY or YYYY-MM or YYYY-MM-DD
import re as _re  # noqa: E402 — stdlib, after module-level constants

_DATE_RE = _re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def _dcid_namespace(dcid: str) -> str:
    """Return the prefix before the first '/', or the whole string if no '/'."""
    slash = dcid.find("/")
    return dcid[:slash] if slash != -1 else dcid


def classify_axis(property_dcid: str, observed_values: list[str]) -> Axis:
    """Classify a constraint property to an axis.

    Resolution order:
    1. AXIS_OVERRIDES — exact match wins.
    2. Date heuristic — if any observed value looks like an ISO date → "when".
    3. Place-fraction auto-rule — if >= 90% of values have a place namespace → "where".
    4. Default → "how".

    Args:
        property_dcid: The constraint property to classify.
        observed_values: A representative sample of values the property takes.
            May be empty; in that case the override or the "how" default applies.

    Returns:
        One of "what", "how", "where", "when".
    """
    if property_dcid in AXIS_OVERRIDES:
        return AXIS_OVERRIDES[property_dcid]

    if not observed_values:
        return "how"

    # Date check — any date-shaped value sends to "when"
    if any(_DATE_RE.match(v) for v in observed_values):
        return "when"

    # Place-fraction check
    place_count = sum(
        1 for v in observed_values if _dcid_namespace(v) in _PLACE_NAMESPACES
    )
    place_frac = place_count / len(observed_values)
    if place_frac >= 0.9:
        return "where"

    return "how"
